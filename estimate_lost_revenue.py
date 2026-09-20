"""
Censored-demand lost revenue estimator.

WHY THIS EXISTS
---------------
The SQL view q2_lost_revenue_by_store prices stockouts off a trailing-7-day average of
units_sold. That method is intuitive and reviewable in a spreadsheet, but it recovers
only ~39% of true lost revenue. Two structural biases, both verified empirically in
src/validate_estimates.py:

  1. SELECTION ON THE DAY. Stockouts do not happen on average days — they happen on
     spike days. Measured here, demand on a stockout day averages 1.7x the trailing
     baseline. An estimator anchored on the mean cannot price the part of the spike
     that exceeds the mean, and recovers only ~27% on partial stockouts.

  2. SELECTION ON THE BASELINE. Days with no stockout are exactly the days demand
     happened to be low, so a mean taken over observed sales is itself biased down.

FIX
---
Treat demand as right-censored, which is what it is: on a stockout day we do not observe
demand, we observe only that demand exceeded the opening stock.

  Step 1  Fit lambda per (store, sku, weekend-flag) by censored-Poisson MLE.
          Uncensored days contribute log pmf(units_sold | lambda);
          censored days contribute log P(D > opening_stock | lambda).
          Weekend is a separate parameter because snacks/beverages carry a real
          Fri-Sat lift (q3) that a single rate would smear across the week.

  Step 2  Price the expected shortage E[(D - S)+] using the fitted lambda and the
          observed opening stock S, and sum it over EVERY store-day.

          Summing over stockout days only is wrong and costs ~45 percentage points of
          coverage: E[(D-S)+] is an unconditional expectation, so restricting the sum to
          days where the shortage was realised conditions on the event being measured.
          Non-stockout days carry small but non-zero expected shortage, and they belong
          in the total. Verified: with oracle lambda, summing over all days recovers
          102% of truth; summing over stockout days only recovers 55%.

Both the naive SQL figure and this corrected figure are reported. The SQL number is the
defensible floor; this one is the planning number.
"""

import os
import sqlite3

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import poisson

DB_PATH = "db/dark_store.db"
OUT_DIR = "data/processed"

LAMBDA_BOUNDS = (1e-3, 60.0)
SHORTAGE_TRUNCATION = 200   # upper summation limit for E[(D-S)+]; lambda here is < 15
WEEKEND_DAYS = (4, 5)       # pandas weekday(): 4=Fri, 5=Sat


# --------------------------------------------------------------------------------------
def fit_censored_poisson(units_sold, opening_stock, censored):
    """
    MLE for the Poisson rate under right-censoring.

    censored[i] is True on days the shelf ran dry, where the only information is
    D_i > opening_stock[i]. The negative log-likelihood is unimodal in lambda, so a
    bounded scalar search is sufficient and needs no gradient.
    """
    if len(units_sold) == 0:
        return LAMBDA_BOUNDS[0]

    uncensored = ~censored
    y_obs = units_sold[uncensored]
    s_cens = opening_stock[censored]

    def nll(lam):
        lam = max(lam, 1e-9)
        total = 0.0
        if y_obs.size:
            total -= poisson.logpmf(y_obs, lam).sum()
        if s_cens.size:
            # sf(S) = P(D > S) — the exact information a stockout day carries.
            total -= np.log(np.clip(poisson.sf(s_cens, lam), 1e-300, 1.0)).sum()
        return total

    return float(minimize_scalar(nll, bounds=LAMBDA_BOUNDS, method="bounded").x)


def expected_shortage(lam, stock, k_max=SHORTAGE_TRUNCATION):
    """E[(D - S)+] for D ~ Poisson(lam), evaluated row-wise."""
    k = np.arange(k_max)
    pmf = poisson.pmf(k[None, :], np.asarray(lam, float)[:, None])
    excess = np.maximum(k[None, :] - np.asarray(stock, float)[:, None], 0.0)
    return (pmf * excess).sum(axis=1)


# --------------------------------------------------------------------------------------
def build(conn=None):
    close = conn is None
    conn = conn or sqlite3.connect(DB_PATH)

    daily = pd.read_sql(
        """
        SELECT i.date, i.store_id, i.sku_id, i.opening_stock, i.units_sold,
               i.stockout_flag, s.category, s.unit_price, s.unit_cost
        FROM inventory_snapshots i
        JOIN skus s ON i.sku_id = s.sku_id
        """,
        conn,
    )
    if close:
        conn.close()

    daily["is_weekend"] = (
        pd.to_datetime(daily.date).dt.weekday.isin(WEEKEND_DAYS).astype(int)
    )

    # Step 1 — fit lambda per (store, sku, weekend)
    fits = []
    for (store_id, sku_id, wknd), g in daily.groupby(
        ["store_id", "sku_id", "is_weekend"], sort=False
    ):
        fits.append(
            (
                store_id,
                sku_id,
                wknd,
                fit_censored_poisson(
                    g.units_sold.values,
                    g.opening_stock.values,
                    g.stockout_flag.values.astype(bool),
                ),
            )
        )
    lam_df = pd.DataFrame(fits, columns=["store_id", "sku_id", "is_weekend", "lambda_mle"])
    daily = daily.merge(lam_df, on=["store_id", "sku_id", "is_weekend"])

    # Step 2 — expected shortage on EVERY store-day
    daily["expected_lost_units"] = expected_shortage(
        daily.lambda_mle.values, daily.opening_stock.values
    )
    daily["expected_lost_revenue"] = daily.expected_lost_units * daily.unit_price
    daily["expected_lost_margin"] = daily.expected_lost_units * (
        daily.unit_price - daily.unit_cost
    )
    return daily


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    daily = build()

    by_store = (
        daily.groupby("store_id")
        .agg(
            n_stockout_days=("stockout_flag", "sum"),
            corrected_lost_units=("expected_lost_units", "sum"),
            corrected_lost_revenue=("expected_lost_revenue", "sum"),
            corrected_lost_margin=("expected_lost_margin", "sum"),
        )
        .round(2)
        .sort_values("corrected_lost_revenue", ascending=False)
        .reset_index()
    )

    by_store_cat = (
        daily.groupby(["store_id", "category"])
        .agg(
            n_stockout_days=("stockout_flag", "sum"),
            corrected_lost_units=("expected_lost_units", "sum"),
            corrected_lost_revenue=("expected_lost_revenue", "sum"),
            corrected_lost_margin=("expected_lost_margin", "sum"),
        )
        .round(2)
        .sort_values("corrected_lost_revenue", ascending=False)
        .reset_index()
    )

    by_store.to_csv(f"{OUT_DIR}/q8_corrected_lost_revenue_by_store.csv", index=False)
    by_store_cat.to_csv(
        f"{OUT_DIR}/q9_corrected_lost_revenue_by_store_category.csv", index=False
    )
    daily.groupby(["store_id", "sku_id", "is_weekend"], as_index=False).lambda_mle.first().to_csv(
        f"{OUT_DIR}/q10_demand_lambda_mle.csv", index=False
    )

    print(f"  Exported q8_corrected_lost_revenue_by_store         {len(by_store):>4} rows")
    print(f"  Exported q9_corrected_lost_revenue_by_store_category {len(by_store_cat):>3} rows")
    print(f"  Exported q10_demand_lambda_mle                      {len(lam := daily.groupby(['store_id','sku_id','is_weekend']))} groups")
    print(
        f"\n  Corrected lost revenue (90d): Rs {by_store.corrected_lost_revenue.sum():,.0f}"
        f"  |  per month: Rs {by_store.corrected_lost_revenue.sum()/3:,.0f}"
    )


if __name__ == "__main__":
    main()
