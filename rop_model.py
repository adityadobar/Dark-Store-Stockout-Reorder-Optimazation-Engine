"""
Phase 3a — reorder-point optimisation model.

Produces every number the Excel workbook and the recommendation memo are built on:

  1. LOSS DECOMPOSITION  — splits the corrected lost revenue (q8) into three buckets with
     different owners and different fixes. This is what stops the memo recommending a
     blanket policy change.

  2. LEAD-TIME INFERENCE — measures actual replenishment lead time from stock-arrival
     timing, so "lead time is the driver" is evidence from the data rather than an
     assumption carried in from the generator.

  3. POLICY ECONOMICS    — revenue recovery vs holding cost vs spoilage across service
     levels, for the targeted segment.

  4. SENSITIVITY GRID    — the two-way Data Table that the Excel model reproduces live.
"""

import os
import sqlite3

import numpy as np
import pandas as pd
from scipy.stats import norm

DB_PATH = "db/dark_store.db"
OUT_DIR = "data/processed"

# --- economic assumptions (every one of these is a named input cell in the workbook) ---
ANNUAL_HOLDING_RATE = 0.25    # carrying cost as a share of unit cost per year
REVIEW_CYCLE_DAYS = 7         # naive policy orders one week of cover
DAYS_PER_MONTH = 30
SERVICE_LEVELS = [0.90, 0.95, 0.99]

# Supplier delay distribution observed at the affected stores (+1d w.p. .6, +2d w.p. .4).
# sigma_LT is small; the model shows the damage is the MEAN shift, not the variance.
DELAY_SIGMA = 0.49

CHRONIC_OPS_STORES = [14, 16]
PROBLEM_STORES_DAIRY = [3, 7, 11]
TARGET_CATEGORY = "dairy"


def normal_loss(z):
    """Unit normal loss function L(z) = phi(z) - z * (1 - Phi(z)). Expected shortage
    per replenishment cycle is sigma_DL * L(z)."""
    return norm.pdf(z) - z * (1 - norm.cdf(z))


# --------------------------------------------------------------------------------------
def infer_lead_times(conn):
    """
    Estimate ACTUAL lead time per (store, category) from observable data only.

    A delivery is a day where opening stock exceeds the prior day's closing stock. The
    lead time is the gap between the first day stock fell to the reorder point and the
    day the replenishment landed.

    The absolute estimate is attenuated (the ROP crossing is detected on a daily grid and
    can lag the true order), so the model uses the PEER-RELATIVE gap: how much longer a
    store waits for a category than other stores wait for the same category. That
    differential is robust to the detection lag, which applies equally to every store.
    """
    d = pd.read_sql(
        """
        SELECT i.date, i.store_id, i.sku_id, i.opening_stock, i.closing_stock,
               s.category, s.lead_time_days
        FROM inventory_snapshots i
        JOIN skus s ON i.sku_id = s.sku_id
        ORDER BY i.store_id, i.sku_id, i.date
        """,
        conn,
    )
    g = d.groupby(["store_id", "sku_id"], sort=False)
    d["prev_close"] = g.closing_stock.shift()
    d["delivery"] = (d.opening_stock > d.prev_close).fillna(False)
    d["day"] = g.cumcount()
    d["consumed"] = (d.prev_close - d.closing_stock).clip(lower=0)

    mu = d.groupby(["store_id", "sku_id"]).consumed.mean().rename("mu_obs")
    d = d.merge(mu, on=["store_id", "sku_id"])
    d["at_rop"] = d.closing_stock <= d.mu_obs * d.lead_time_days

    rows = []
    for (store_id, sku_id), grp in d.groupby(["store_id", "sku_id"], sort=False):
        trigger = None
        for r in grp.itertuples():
            if r.delivery and trigger is not None:
                rows.append((store_id, r.category, r.lead_time_days, r.day - trigger))
                trigger = None
            if trigger is None and r.at_rop:
                trigger = r.day

    lt = pd.DataFrame(rows, columns=["store_id", "category", "nominal_lt", "observed_lt"])
    lt = lt[(lt.observed_lt >= 1) & (lt.observed_lt <= 15)]

    cell = (
        lt.groupby(["store_id", "category"])
        .agg(nominal_lt=("nominal_lt", "mean"),
             observed_lt=("observed_lt", "mean"),
             n_cycles=("observed_lt", "size"))
        .reset_index()
    )
    peer = cell.groupby("category").observed_lt.median().rename("peer_observed_lt")
    cell = cell.merge(peer, on="category")
    cell["excess_lead_time_days"] = (cell.observed_lt - cell.peer_observed_lt).round(3)
    return cell.round(3)


# --------------------------------------------------------------------------------------
def decompose_losses():
    """Split corrected lost revenue into buckets that have different owners."""
    q9 = pd.read_csv(f"{OUT_DIR}/q9_corrected_lost_revenue_by_store_category.csv")

    def bucket(r):
        if r.store_id in CHRONIC_OPS_STORES:
            return "A_ops_shrinkage"
        if r.store_id in PROBLEM_STORES_DAIRY and r.category == TARGET_CATEGORY:
            return "B_leadtime_mismatch"
        return "C_systemic_no_safety_stock"

    q9["bucket"] = q9.apply(bucket, axis=1)
    out = (
        q9.groupby("bucket")
        .agg(lost_revenue_90d=("corrected_lost_revenue", "sum"),
             lost_margin_90d=("corrected_lost_margin", "sum"),
             n_cells=("store_id", "size"))
        .reset_index()
    )
    out["lost_revenue_monthly"] = (out.lost_revenue_90d / 3).round(0)
    out["lost_margin_monthly"] = (out.lost_margin_90d / 3).round(0)
    out["pct_of_total"] = (out.lost_revenue_90d / out.lost_revenue_90d.sum() * 100).round(1)
    return out.sort_values("lost_revenue_90d", ascending=False).round(2)


# --------------------------------------------------------------------------------------
def segment_parameters(conn, lead_times):
    """Representative-SKU parameters for the targeted segment (dairy @ 3 stores)."""
    lam = pd.read_csv(f"{OUT_DIR}/q10_demand_lambda_mle.csv")
    lam = lam.groupby(["store_id", "sku_id"]).lambda_mle.mean().reset_index()

    sku = pd.read_sql("SELECT sku_id, category, unit_cost, unit_price, "
                      "shelf_life_days, lead_time_days FROM skus", conn)
    lam = lam.merge(sku, on="sku_id")
    seg = lam[(lam.category == TARGET_CATEGORY)
              & (lam.store_id.isin(PROBLEM_STORES_DAIRY))]

    lt_seg = lead_times[(lead_times.category == TARGET_CATEGORY)
                        & (lead_times.store_id.isin(PROBLEM_STORES_DAIRY))]

    mu = float(seg.lambda_mle.mean())
    return {
        "n_stores": len(PROBLEM_STORES_DAIRY),
        "n_skus_per_store": int(seg.groupby("store_id").sku_id.nunique().mean()),
        "mu_daily_units": round(mu, 4),
        "sigma_daily_units": round(np.sqrt(mu), 4),   # Poisson demand
        "nominal_lead_time": round(float(lt_seg.nominal_lt.mean()), 3),
        "excess_lead_time": round(float(lt_seg.excess_lead_time_days.mean()), 3),
        "sigma_lead_time": DELAY_SIGMA,
        "unit_cost": round(float(seg.unit_cost.mean()), 2),
        "unit_price": round(float(seg.unit_price.mean()), 2),
        "unit_margin": round(float((seg.unit_price - seg.unit_cost).mean()), 2),
        "shelf_life_days": round(float(seg.shelf_life_days.mean()), 1),
    }


def policy_economics(p, observed_lost_units_per_sku_month, measured_revenue_loss,
                     measured_margin_loss):
    """
    Evaluate the current policy and each candidate service level.

    Current policy sizes the reorder point on the NOMINAL lead time while the goods
    actually take nominal + excess. That mismatch is a negative safety stock: the model
    expresses it as z_current < 0, which is why the store is structurally short.
    """
    mu, sd = p["mu_daily_units"], p["sigma_daily_units"]
    l_actual = p["nominal_lead_time"] + p["excess_lead_time"]

    # Demand-over-lead-time spread, with stochastic lead time:
    #   sigma_DL = sqrt(L * sigma_d^2 + mu^2 * sigma_LT^2)
    var_demand = l_actual * sd ** 2
    var_leadtime = mu ** 2 * p["sigma_lead_time"] ** 2
    sigma_dl = np.sqrt(var_demand + var_leadtime)

    rop_current = mu * p["nominal_lead_time"]
    z_current = (rop_current - mu * l_actual) / sigma_dl
    cycles = DAYS_PER_MONTH / REVIEW_CYCLE_DAYS

    # Calibrate the analytical model to the loss actually observed under today's policy,
    # then use it only for RELATIVE improvement. The raw model overstates absolute
    # shortage because it assumes a fresh exposure window every cycle.
    model_baseline = sigma_dl * normal_loss(z_current) * cycles
    calibration = observed_lost_units_per_sku_month / model_baseline

    order_qty = mu * REVIEW_CYCLE_DAYS
    monthly_holding_rate = ANNUAL_HOLDING_RATE / 12
    scale = p["n_stores"] * p["n_skus_per_store"]

    rows = []
    for label, sl in [("Current (naive, no safety stock)", None)] + [
        (f"Policy {int(s*100)}% service", s) for s in SERVICE_LEVELS
    ]:
        if sl is None:
            z, rop = z_current, rop_current
        else:
            z = norm.ppf(sl)
            rop = mu * l_actual + z * sigma_dl

        safety_stock = rop - mu * l_actual
        lost_units = sigma_dl * normal_loss(z) * cycles * calibration
        recovered = observed_lost_units_per_sku_month - lost_units

        # Anchor value recovered to the MEASURED segment loss (q9) rather than repricing
        # a representative SKU. Pricing recovered units at the segment's mean unit_price
        # drifts a few percent away from the measured loss — enough to project recovering
        # more than was lost, which is not a claim the model can support.
        recovery_share = recovered / observed_lost_units_per_sku_month

        avg_inventory = order_qty / 2 + safety_stock
        holding = avg_inventory * p["unit_cost"] * monthly_holding_rate

        # Spoilage: stock that cannot clear within shelf life ages out each cycle.
        excess = max(0.0, avg_inventory - p["shelf_life_days"] * mu)
        spoilage = excess * p["unit_cost"] * cycles

        rows.append({
            "policy": label,
            "service_level_pct": round(sl * 100, 1) if sl else round(norm.cdf(z) * 100, 1),
            "z": round(z, 3),
            "reorder_point_units": round(rop, 2),
            "safety_stock_units": round(safety_stock, 2),
            "lost_units_per_sku_month": round(lost_units, 3),
            "fill_rate_pct": round((1 - lost_units / (mu * DAYS_PER_MONTH)) * 100, 2),
            "recovery_share_pct": round(recovery_share * 100, 2),
            "recovered_units_month": round(recovered * scale, 1),
            "revenue_recovered_month": round(recovery_share * measured_revenue_loss, 0),
            "margin_recovered_month": round(recovery_share * measured_margin_loss, 0),
            "holding_cost_month": round(holding * scale, 0),
            "spoilage_cost_month": round(spoilage * scale, 0),
        })

    df = pd.DataFrame(rows)
    base_hold = df.loc[0, "holding_cost_month"]
    base_spoil = df.loc[0, "spoilage_cost_month"]
    df["incremental_holding_month"] = (df.holding_cost_month - base_hold).round(0)
    df["incremental_spoilage_month"] = (df.spoilage_cost_month - base_spoil).round(0)
    df["net_margin_impact_month"] = (
        df.margin_recovered_month - df.incremental_holding_month - df.incremental_spoilage_month
    ).round(0)

    meta = {
        "sigma_dl": round(sigma_dl, 4),
        "var_share_demand": round(var_demand / (var_demand + var_leadtime), 4),
        "var_share_leadtime": round(var_leadtime / (var_demand + var_leadtime), 4),
        "l_actual": round(l_actual, 3),
        "z_current": round(z_current, 4),
        "calibration": round(calibration, 4),
        "cycles_per_month": round(cycles, 4),
        "units_short_per_cycle_from_leadtime": round(mu * p["excess_lead_time"], 3),
        # Newsvendor critical ratio: optimal cycle service level given the real economics.
        "critical_ratio": None,
    }
    holding_per_unit_cycle = (
        p["unit_cost"] * ANNUAL_HOLDING_RATE * REVIEW_CYCLE_DAYS / 365
    )
    meta["holding_cost_per_unit_per_cycle"] = round(holding_per_unit_cycle, 4)
    meta["critical_ratio"] = round(
        p["unit_margin"] / (p["unit_margin"] + holding_per_unit_cycle), 4
    )
    meta["optimal_service_level_pct"] = round(meta["critical_ratio"] * 100, 2)
    return df, meta


def sensitivity_grid(p, observed, meta):
    """Two-way sensitivity: service level x excess lead time. Mirrors the Excel Data Table."""
    mu, sd = p["mu_daily_units"], p["sigma_daily_units"]
    scale = p["n_stores"] * p["n_skus_per_store"]
    cycles = DAYS_PER_MONTH / REVIEW_CYCLE_DAYS
    monthly_holding_rate = ANNUAL_HOLDING_RATE / 12
    order_qty = mu * REVIEW_CYCLE_DAYS

    service_axis = [0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99]
    excess_axis = [0.0, 0.5, 1.0, 1.5, 2.0]

    grid = []
    for excess in excess_axis:
        l_act = p["nominal_lead_time"] + excess
        sigma_dl = np.sqrt(l_act * sd ** 2 + mu ** 2 * p["sigma_lead_time"] ** 2)
        z_cur = (mu * p["nominal_lead_time"] - mu * l_act) / sigma_dl
        base = sigma_dl * normal_loss(z_cur) * cycles * meta["calibration"]
        row = {"excess_lead_time_days": excess}
        for sl in service_axis:
            z = norm.ppf(sl)
            lost = sigma_dl * normal_loss(z) * cycles * meta["calibration"]
            ss = z * sigma_dl
            recovered = (base - lost) * scale
            holding = ((order_qty / 2 + ss) - (order_qty / 2 + z_cur * sigma_dl)) \
                * p["unit_cost"] * monthly_holding_rate * scale
            row[f"sl_{int(sl*1000)}"] = round(
                recovered * p["unit_margin"] - holding, 0
            )
        grid.append(row)
    return pd.DataFrame(grid), service_axis, excess_axis


def network_rollout(conn, lead_times, service_level=0.95):
    """
    Project the same reorder-point policy across every store x category cell, EXCLUDING
    the chronic-ops stores.

    Those two stores are excluded on purpose. Their stock disappears without being sold,
    so a calibrated reorder model would happily attribute their losses to reorder policy
    and forecast a recovery that raising the reorder point cannot deliver — it would just
    fund more shrinkage. They belong to an operations workstream, not this one.
    """
    lam = pd.read_csv(f"{OUT_DIR}/q10_demand_lambda_mle.csv")
    lam = lam.groupby(["store_id", "sku_id"]).lambda_mle.mean().reset_index()
    sku = pd.read_sql("SELECT sku_id, category, unit_cost, unit_price FROM skus", conn)
    lam = lam.merge(sku, on="sku_id")

    cells = (
        lam.groupby(["store_id", "category"])
        .agg(mu=("lambda_mle", "mean"), n_skus=("sku_id", "nunique"),
             unit_cost=("unit_cost", "mean"))
        .reset_index()
    )
    cells = cells.merge(
        lead_times[["store_id", "category", "nominal_lt", "excess_lead_time_days"]],
        on=["store_id", "category"], how="left",
    ).fillna({"excess_lead_time_days": 0.0})

    q9 = pd.read_csv(f"{OUT_DIR}/q9_corrected_lost_revenue_by_store_category.csv")
    cells = cells.merge(
        q9[["store_id", "category", "corrected_lost_revenue", "corrected_lost_margin"]],
        on=["store_id", "category"], how="left",
    )
    cells = cells[~cells.store_id.isin(CHRONIC_OPS_STORES)].copy()

    cycles = DAYS_PER_MONTH / REVIEW_CYCLE_DAYS
    monthly_holding_rate = ANNUAL_HOLDING_RATE / 12
    z_target = norm.ppf(service_level)

    excess = cells.excess_lead_time_days.clip(lower=0)
    l_actual = cells.nominal_lt + excess
    sigma_dl = np.sqrt(l_actual * cells.mu + cells.mu ** 2 * DELAY_SIGMA ** 2)
    z_current = (cells.mu * cells.nominal_lt - cells.mu * l_actual) / sigma_dl

    base = sigma_dl * np.array([normal_loss(z) for z in z_current]) * cycles
    new = sigma_dl * normal_loss(z_target) * cycles
    cells["recovery_share"] = ((base - new) / base).clip(0, 1)

    # Measured monthly loss for the cell (q9 covers 90 days)
    cells["revenue_recovered_month"] = (
        cells.recovery_share * cells.corrected_lost_revenue / 3
    )
    cells["margin_recovered_month"] = (
        cells.recovery_share * cells.corrected_lost_margin / 3
    )
    delta_ss = (z_target * sigma_dl) - (z_current * sigma_dl)
    cells["holding_cost_month"] = (
        delta_ss * cells.unit_cost * monthly_holding_rate * cells.n_skus
    )
    cells["net_margin_impact_month"] = (
        cells.margin_recovered_month - cells.holding_cost_month
    )
    return cells.round(2)


# --------------------------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    print("1. Inferring actual lead times from stock-arrival timing ...")
    lead_times = infer_lead_times(conn)
    lead_times.to_csv(f"{OUT_DIR}/q11_inferred_lead_times.csv", index=False)
    seg_lt = lead_times[(lead_times.category == TARGET_CATEGORY)
                        & (lead_times.store_id.isin(PROBLEM_STORES_DAIRY))]
    print(f"   dairy @ stores {PROBLEM_STORES_DAIRY}: "
          f"+{seg_lt.excess_lead_time_days.mean():.2f} days vs peer stores")

    print("\n2. Decomposing lost revenue ...")
    buckets = decompose_losses()
    buckets.to_csv(f"{OUT_DIR}/q12_loss_decomposition.csv", index=False)
    print(buckets[["bucket", "lost_revenue_monthly", "pct_of_total", "n_cells"]]
          .to_string(index=False))

    print("\n3. Building policy economics ...")
    p = segment_parameters(conn, lead_times)

    # Observed loss for the targeted segment, per SKU per month (from the corrected model)
    q9 = pd.read_csv(f"{OUT_DIR}/q9_corrected_lost_revenue_by_store_category.csv")
    seg_loss = q9[(q9.category == TARGET_CATEGORY)
                  & (q9.store_id.isin(PROBLEM_STORES_DAIRY))]
    observed = (seg_loss.corrected_lost_units.sum()
                / (p["n_stores"] * p["n_skus_per_store"]) / 3)
    measured_rev = seg_loss.corrected_lost_revenue.sum() / 3
    measured_mgn = seg_loss.corrected_lost_margin.sum() / 3

    policies, meta = policy_economics(p, observed, measured_rev, measured_mgn)
    grid, service_axis, excess_axis = sensitivity_grid(p, observed, meta)

    pd.DataFrame([p]).to_csv(f"{OUT_DIR}/q13_segment_parameters.csv", index=False)
    pd.DataFrame([meta]).to_csv(f"{OUT_DIR}/q14_model_meta.csv", index=False)
    policies.to_csv(f"{OUT_DIR}/q15_policy_economics.csv", index=False)
    grid.to_csv(f"{OUT_DIR}/q16_sensitivity_grid.csv", index=False)

    print(f"   sigma_DL = {meta['sigma_dl']}  "
          f"(demand {meta['var_share_demand']:.1%} / lead time {meta['var_share_leadtime']:.1%})")
    print(f"   ROP sized on {p['nominal_lead_time']}d, goods arrive in {meta['l_actual']}d "
          f"-> {meta['units_short_per_cycle_from_leadtime']} units short per cycle")
    print(f"   newsvendor optimal service level = {meta['optimal_service_level_pct']}%")
    print()
    print(policies[["policy", "reorder_point_units", "safety_stock_units", "fill_rate_pct",
                    "revenue_recovered_month", "incremental_holding_month",
                    "net_margin_impact_month"]].to_string(index=False))

    print("\n4. Projecting network-wide rollout (95% service, ex chronic-ops stores) ...")
    net = network_rollout(conn, lead_times, service_level=0.95)
    net.to_csv(f"{OUT_DIR}/q17_network_rollout.csv", index=False)
    print(f"   cells modelled          : {len(net)} (16 stores x 6 categories)")
    print(f"   revenue recovered/month : Rs {net.revenue_recovered_month.sum():,.0f}")
    print(f"   margin recovered/month  : Rs {net.margin_recovered_month.sum():,.0f}")
    print(f"   holding cost/month      : Rs {net.holding_cost_month.sum():,.0f}")
    print(f"   NET margin impact/month : Rs {net.net_margin_impact_month.sum():,.0f}")
    conn.close()


if __name__ == "__main__":
    main()
