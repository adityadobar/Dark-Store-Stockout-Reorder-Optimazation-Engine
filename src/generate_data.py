"""
Phase 1 — Synthetic data generation for the Dark Store Reorder Optimization Model.

Simulates 90 days of demand, inventory and replenishment across 18 dark stores x 180 SKUs
under a deliberately NAIVE reorder policy (reorder point = mean demand over lead time,
zero safety stock). Three patterns are injected on top of the naive baseline so that the
downstream SQL analysis has a known ground truth to re-derive:

  1. PROBLEM_STORES_DAIRY   - recurring supplier lead-time slippage on dairy only
  2. CHRONIC_OPS_STORES     - daily inventory shrinkage across all categories
  3. Weekend demand lift    - Fri/Sat uplift on snacks + beverages only

See docs/data_generation_notes.md for the answer key.
"""

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
RNG_SEED = 42

N_STORES = 18
N_SKUS = 180
N_DAYS = 90
START_DATE = date(2026, 1, 1)

CITIES = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune", "Chennai"]
ZONES = ["north", "south", "east", "west", "central"]

CATEGORIES = ["dairy", "produce", "snacks", "personal_care", "household", "beverages"]

CATEGORY_SHELF_LIFE = {  # days
    "dairy": 7,
    "produce": 5,
    "snacks": 180,
    "personal_care": 365,
    "household": 365,
    "beverages": 270,
}

# (min, max) inclusive lead time in days. Dairy is the tightest / most fragile chain.
CATEGORY_LEAD_TIME_RANGE = {
    "dairy": (2, 4),
    "produce": (1, 3),
    "snacks": (1, 2),
    "personal_care": (1, 2),
    "household": (1, 2),
    "beverages": (1, 2),
}

CATEGORY_COST_RANGE = {  # (min, max) unit cost in INR
    "dairy": (20, 80),
    "produce": (20, 80),
    "snacks": (25, 150),
    "personal_care": (60, 400),
    "household": (50, 400),
    "beverages": (25, 180),
}

# --- injected patterns (this block is the answer key) ---------------------------------
PROBLEM_STORES_DAIRY = [3, 7, 11]   # structurally elevated dairy stockouts (supplier delay)
CHRONIC_OPS_STORES = [14, 16]       # elevated stockouts across ALL categories (shrinkage)
WEEKEND_DEMAND_MULTIPLIER = 1.35    # Fri/Sat, snacks + beverages only
WEEKEND_DAYS = (4, 5)               # Python weekday(): 4=Fri, 5=Sat

SUPPLIER_DELAY_CHOICES = [1, 2]     # extra days added to dairy lead time at problem stores
SUPPLIER_DELAY_PROBS = [0.6, 0.4]
SHRINKAGE_LAMBDA = 1.5              # mean units lost/day/SKU at chronic ops stores

REORDER_COVER_DAYS = 7              # naive order quantity = 7 days of mean demand
INITIAL_STOCK_DAYS = 10             # day-0 opening stock = 10 days of mean demand

DELIVERY_TIME_MEAN = 18.0           # minutes
DELIVERY_TIME_SD = 5.0
DELIVERY_TIME_BOUNDS = (8.0, 45.0)
DELIVERY_PENALTY_ON_STOCKOUT = 8.0  # stockouts force substitution/repick -> slower delivery

RAW_DIR = os.path.join("data", "raw")


# --------------------------------------------------------------------------------------
# Dimension tables
# --------------------------------------------------------------------------------------
def generate_dark_stores(n=N_STORES):
    """One row per dark store. avg_daily_orders sets the baseline demand scale."""
    rng = np.random.default_rng(RNG_SEED)
    return pd.DataFrame(
        {
            "store_id": np.arange(1, n + 1),
            "city": rng.choice(CITIES, size=n),
            "zone": rng.choice(ZONES, size=n),
            "size_sqft": rng.integers(800, 2500, size=n),
            "avg_daily_orders": rng.integers(150, 600, size=n),
        }
    )


def generate_skus(n=N_SKUS):
    """
    One row per SKU. popularity_weight is exponential so a handful of SKUs are
    high-velocity and the long tail is slow-moving — which is what makes the
    stockout distribution realistic rather than uniform.
    """
    rng = np.random.default_rng(RNG_SEED + 1)

    # Roughly even split across categories (180 / 6 = 30 each), then shuffled.
    category = np.repeat(CATEGORIES, n // len(CATEGORIES))
    rng.shuffle(category)

    unit_cost = np.array(
        [round(rng.uniform(*CATEGORY_COST_RANGE[c]), 2) for c in category]
    )
    unit_price = np.round(unit_cost * rng.uniform(1.3, 1.8, size=n), 2)

    shelf_life = np.array(
        [max(2, CATEGORY_SHELF_LIFE[c] + int(rng.integers(-2, 3))) for c in category]
    )
    lead_time = np.array(
        [int(rng.integers(CATEGORY_LEAD_TIME_RANGE[c][0],
                          CATEGORY_LEAD_TIME_RANGE[c][1] + 1)) for c in category]
    )
    popularity = np.round(rng.exponential(1.0, size=n) + 0.05, 4)

    return pd.DataFrame(
        {
            "sku_id": np.arange(1, n + 1),
            "category": category,
            "unit_cost": unit_cost,
            "unit_price": unit_price,
            "shelf_life_days": shelf_life,
            "lead_time_days": lead_time,
            "popularity_weight": popularity,
        }
    )


def base_daily_demand(store_row, sku_row):
    """Deterministic mean demand for a (store, sku) pair, before noise/weekend/injections."""
    base = store_row.avg_daily_orders * sku_row.popularity_weight / N_SKUS
    return max(base, 0.3)


# --------------------------------------------------------------------------------------
# Core simulation
# --------------------------------------------------------------------------------------
def generate_inventory_and_orders(stores_df, skus_df):
    """
    Sequential inventory state machine per (store, sku) pair over N_DAYS.

    Daily sequence:
        receive arrivals -> apply shrinkage -> record opening -> serve demand
        -> record closing -> evaluate reorder

    The reorder point carries NO safety stock (reorder_point = mean_demand * lead_time),
    so roughly half of all replenishment cycles run dry before the truck lands. That is
    the structural flaw the Phase 3 Excel model fixes.
    """
    rng = np.random.default_rng(RNG_SEED + 2)

    dates = [START_DATE + timedelta(days=d) for d in range(N_DAYS)]
    date_strs = [d.isoformat() for d in dates]
    weekdays = np.array([d.weekday() for d in dates])

    inv_rows = []
    order_rows = []

    stores = list(stores_df.itertuples(index=False))
    skus = list(skus_df.itertuples(index=False))

    for store in stores:
        is_chronic = store.store_id in CHRONIC_OPS_STORES
        is_dairy_problem = store.store_id in PROBLEM_STORES_DAIRY

        for sku in skus:
            mean_demand = base_daily_demand(store, sku)
            lead_time = sku.lead_time_days

            # Planner's naive parameters (uses nominal lead time, no safety stock)
            reorder_point = mean_demand * lead_time
            order_qty = max(1, int(round(mean_demand * REORDER_COVER_DAYS)))

            # Pre-draw the stochastic components for the whole horizon (vectorized).
            # The weekend lift scales the Poisson RATE, not the realised count: scaling
            # the count and truncating back to int biases the uplift down by ~half a unit
            # per day, which on a mean of ~2 units/day would show up as a ~20% lift
            # instead of the intended 35%.
            lam = np.full(N_DAYS, mean_demand, dtype=float)
            if sku.category in ("snacks", "beverages"):
                lam[np.isin(weekdays, WEEKEND_DAYS)] *= WEEKEND_DEMAND_MULTIPLIER
            demand_series = rng.poisson(lam).astype(int)

            shrinkage_series = (
                rng.poisson(SHRINKAGE_LAMBDA, size=N_DAYS) if is_chronic
                else np.zeros(N_DAYS, dtype=int)
            )

            on_hand = int(round(mean_demand * INITIAL_STOCK_DAYS))
            pending_qty = 0
            pending_arrival_day = None

            for d in range(N_DAYS):
                # 1. Receive inbound replenishment
                if pending_arrival_day is not None and d >= pending_arrival_day:
                    on_hand += pending_qty
                    pending_qty = 0
                    pending_arrival_day = None

                # 2. Operational leakage (chronic ops stores only) — independent of demand
                if is_chronic:
                    on_hand = max(on_hand - int(shrinkage_series[d]), 0)

                opening_stock = on_hand

                # 3. Serve demand
                units_demanded = int(demand_series[d])
                units_sold = min(units_demanded, max(opening_stock, 0))
                stockout_flag = 1 if units_demanded > opening_stock else 0
                closing_stock = max(opening_stock - units_sold, 0)
                on_hand = closing_stock

                inv_rows.append(
                    (
                        date_strs[d], store.store_id, sku.sku_id,
                        opening_stock, units_demanded, units_sold,
                        closing_stock, stockout_flag,
                    )
                )

                # 4. Naive reorder check
                if on_hand <= reorder_point and pending_arrival_day is None:
                    effective_lead_time = lead_time
                    if is_dairy_problem and sku.category == "dairy":
                        # Recurring supplier slippage: the truck lands 1-2 days after the
                        # lead time the reorder point was sized on.
                        effective_lead_time += int(
                            rng.choice(SUPPLIER_DELAY_CHOICES, p=SUPPLIER_DELAY_PROBS)
                        )
                    pending_qty = order_qty
                    pending_arrival_day = d + effective_lead_time

                # 5. Customer order lines (baskets of 1-3 units)
                remaining_demand = units_demanded
                while remaining_demand > 0:
                    line_qty = min(int(rng.integers(1, 4)), remaining_demand)
                    remaining_demand -= line_qty
                    # Lines are served first-come-first-served out of opening stock.
                    served_before = units_demanded - remaining_demand - line_qty
                    fulfilled = 1 if (served_before + line_qty) <= units_sold else 0
                    dt_mean = DELIVERY_TIME_MEAN + (
                        DELIVERY_PENALTY_ON_STOCKOUT if stockout_flag else 0.0
                    )
                    delivery_time = float(
                        np.clip(rng.normal(dt_mean, DELIVERY_TIME_SD), *DELIVERY_TIME_BOUNDS)
                    )
                    order_rows.append(
                        (
                            store.store_id, sku.sku_id, date_strs[d],
                            line_qty, fulfilled, round(delivery_time, 1),
                        )
                    )

    inventory_df = pd.DataFrame(
        inv_rows,
        columns=[
            "date", "store_id", "sku_id", "opening_stock",
            "units_demanded", "units_sold", "closing_stock", "stockout_flag",
        ],
    )
    orders_df = pd.DataFrame(
        order_rows,
        columns=[
            "store_id", "sku_id", "order_timestamp",
            "qty", "fulfilled_flag", "delivery_time_minutes",
        ],
    )
    return inventory_df, orders_df


# --------------------------------------------------------------------------------------
def main():
    os.makedirs(RAW_DIR, exist_ok=True)

    stores_df = generate_dark_stores()
    skus_df = generate_skus()
    inventory_df, orders_df = generate_inventory_and_orders(stores_df, skus_df)

    stores_df.to_csv(f"{RAW_DIR}/dark_stores.csv", index=False)
    skus_df.to_csv(f"{RAW_DIR}/skus.csv", index=False)
    inventory_df.to_csv(f"{RAW_DIR}/inventory_snapshots.csv", index=False)
    orders_df.to_csv(f"{RAW_DIR}/orders.csv", index=False)

    print(
        f"Generated {len(stores_df)} stores, {len(skus_df)} skus, "
        f"{len(inventory_df):,} inventory rows, {len(orders_df):,} order rows."
    )
    print(f"Overall stockout rate: {inventory_df.stockout_flag.mean() * 100:.2f}%")


if __name__ == "__main__":
    main()
