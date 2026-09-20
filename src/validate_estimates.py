"""
Validation gate — run after run_queries.py, before building anything in Excel.

Two jobs:

  A. ANSWER KEY. Confirm the SQL re-derives the injected patterns from observable data
     alone. If a check fails, the bug is in the simulation or the query logic and the
     memo would be built on sand.

  B. ESTIMATOR ACCURACY. q2 infers lost revenue from a trailing-7-day baseline because
     unmet demand is never logged. The generator DID record true demand (withheld from
     the warehouse), so the estimate can be scored against it here. This is the number
     that tells you how much to trust the headline.
"""

import sqlite3

import pandas as pd

from generate_data import (
    CHRONIC_OPS_STORES,
    PROBLEM_STORES_DAIRY,
    WEEKEND_DEMAND_MULTIPLIER,
)

DB_PATH = "db/dark_store.db"
results = []


def check(name, passed, detail):
    results.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}\n         {detail}")


def main():
    conn = sqlite3.connect(DB_PATH)
    q1 = pd.read_sql("SELECT * FROM q1_stockout_rate", conn)
    q3 = pd.read_sql("SELECT * FROM q3_weekend_demand_delta", conn)
    q4 = pd.read_sql("SELECT * FROM q4_leadtime_vs_stockout", conn)
    q5 = pd.read_sql("SELECT * FROM q5_stockout_root_cause", conn)
    q2 = pd.read_sql("SELECT * FROM q2_lost_revenue_by_store", conn)
    conn.close()

    print("\nA. ANSWER-KEY CHECKS")
    print("-" * 78)

    # A1 — dairy problem stores must top the dairy stockout table (excluding the
    # chronic-ops stores, which are elevated everywhere for a different reason).
    dairy = q1[q1.category == "dairy"].set_index("store_id").stockout_rate_pct
    baseline = dairy.drop(PROBLEM_STORES_DAIRY + CHRONIC_OPS_STORES).mean()
    flagged = dairy.loc[PROBLEM_STORES_DAIRY]
    check(
        "A1 dairy problem stores detected",
        bool((flagged > 2 * baseline).all()),
        f"stores {PROBLEM_STORES_DAIRY} at {flagged.round(1).tolist()}% "
        f"vs {baseline:.1f}% peer baseline ({(flagged.mean()/baseline):.1f}x)",
    )

    # A2 — chronic ops stores must be elevated in EVERY category, not just one.
    by_store = q1.groupby("store_id").stockout_rate_pct.mean()
    peer = by_store.drop(CHRONIC_OPS_STORES + PROBLEM_STORES_DAIRY).mean()
    breadth = (
        q1[q1.store_id.isin(CHRONIC_OPS_STORES)]
        .groupby("store_id")
        .apply(lambda g: (g.stockout_rate_pct > 2 * peer).sum(), include_groups=False)
    )
    check(
        "A2 chronic ops stores elevated across ALL categories",
        bool((breadth == 6).all()),
        f"stores {CHRONIC_OPS_STORES} exceed 2x peer in "
        f"{breadth.tolist()} of 6 categories (peer avg {peer:.1f}%)",
    )

    # A3 — weekend lift confined to snacks + beverages, near the injected multiplier.
    lift = q3.set_index("category").weekend_lift_pct
    target = (WEEKEND_DEMAND_MULTIPLIER - 1) * 100
    boosted = lift.loc[["snacks", "beverages"]]
    others = lift.drop(["snacks", "beverages"])
    check(
        "A3 weekend lift isolated to snacks + beverages",
        bool((boosted > 25).all() and others.abs().max() < 5),
        f"snacks/beverages +{boosted.round(1).tolist()}% (injected +{target:.0f}%, "
        f"attenuated by stockout censoring); all others within "
        f"±{others.abs().max():.1f}%",
    )

    # A4 — stockout rate must rise monotonically with lead time.
    s = q4.sort_values("lead_time_days").stockout_rate_pct
    check(
        "A4 stockout rate rises monotonically with lead time",
        bool(s.is_monotonic_increasing),
        f"{s.tolist()}% across lead times {q4.lead_time_days.tolist()} days "
        f"({s.iloc[-1]/s.iloc[0]:.1f}x from 1d to 4d)",
    )

    # A5 — root-cause split must flag the injected stores as supply-driven.
    piv = q5.pivot(index="store_id", columns="root_cause", values="n_stockout_events")
    supply_pct = (piv.supply_driven / piv.sum(axis=1) * 100).round(1)
    clean = supply_pct.drop(CHRONIC_OPS_STORES + PROBLEM_STORES_DAIRY)
    check(
        "A5 root-cause split isolates injected stores as supply-driven",
        bool(
            supply_pct.loc[CHRONIC_OPS_STORES].min() > supply_pct.loc[PROBLEM_STORES_DAIRY].max()
            and supply_pct.loc[PROBLEM_STORES_DAIRY].min() > clean.max()
        ),
        f"chronic {supply_pct.loc[CHRONIC_OPS_STORES].tolist()}% > "
        f"dairy {supply_pct.loc[PROBLEM_STORES_DAIRY].tolist()}% > "
        f"clean peers max {clean.max():.1f}%",
    )

    print("\nB. ESTIMATOR ACCURACY (vs withheld ground truth)")
    print("-" * 78)

    raw = pd.read_csv("data/raw/inventory_snapshots.csv")
    skus = pd.read_csv("data/raw/skus.csv")[["sku_id", "unit_price"]]
    raw = raw.merge(skus, on="sku_id")
    raw["true_lost_units"] = (raw.units_demanded - raw.units_sold).clip(lower=0)
    raw["true_lost_revenue"] = raw.true_lost_units * raw.unit_price
    truth = raw.groupby("store_id").true_lost_revenue.sum()

    naive = q2.set_index("store_id").estimated_lost_revenue
    corrected = (
        pd.read_csv("data/processed/q8_corrected_lost_revenue_by_store.csv")
        .set_index("store_id")
        .corrected_lost_revenue
    )

    print(f"  {'':<26}{'total (90d)':>16}{'coverage':>11}{'Pearson r':>12}")
    for label, est in (("naive trailing-7d (q2)", naive), ("censored-Poisson (q8)", corrected)):
        print(
            f"  {label:<26}{'Rs ' + format(est.sum(), ',.0f'):>16}"
            f"{est.sum()/truth.sum():>10.1%}{truth.corr(est):>12.4f}"
        )
    print(f"  {'ground truth':<26}{'Rs ' + format(truth.sum(), ',.0f'):>16}")

    check(
        "B1 naive SQL estimate is a conservative floor",
        naive.sum() < truth.sum(),
        f"q2 recovers {naive.sum()/truth.sum():.1%} of truth — safe to quote as a "
        f"lower bound, unsafe to quote as the headline",
    )
    check(
        "B2 censored-Poisson estimate is materially unbiased",
        0.90 <= corrected.sum() / truth.sum() <= 1.10,
        f"q8 recovers {corrected.sum()/truth.sum():.1%} of truth "
        f"(Rs {corrected.sum():,.0f} vs Rs {truth.sum():,.0f})",
    )

    # The decision the analysis actually drives is "which stores do we touch?", so the
    # ranking test that matters is set overlap at the top, not a global rank correlation.
    # Among the 13 unaffected stores true lost revenue is statistically flat, and
    # shuffling exchangeable stores would fail a Spearman threshold for no real reason.
    top5_true = set(truth.nlargest(5).index)
    top5_est = set(corrected.nlargest(5).index)
    check(
        "B3 top-5 targeting set recovered exactly",
        top5_true == top5_est,
        f"estimate picks stores {sorted(top5_est)}; truth says {sorted(top5_true)}",
    )

    print("\n" + "=" * 78)
    n_pass = sum(p for _, p, _ in results)
    print(f"  {n_pass}/{len(results)} checks passed")
    print("=" * 78)
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
