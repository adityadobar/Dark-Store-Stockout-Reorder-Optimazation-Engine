-- ======================================================================================
-- Phase 2 — analysis views.
--
-- Q1-Q5 are the core diagnostic set. Q6-Q7 are extensions that feed the Phase 3 Excel
-- reorder-point model. Every view works from observable data only (no true-demand column
-- exists in the warehouse — see sql/schema.sql).
-- ======================================================================================

DROP VIEW IF EXISTS q1_stockout_rate;
DROP VIEW IF EXISTS q2_daily_with_rolling_avg;
DROP VIEW IF EXISTS q2_lost_revenue_by_store;
DROP VIEW IF EXISTS q3_weekend_demand_delta;
DROP VIEW IF EXISTS q4_leadtime_vs_stockout;
DROP VIEW IF EXISTS q5_stockout_root_cause;
DROP VIEW IF EXISTS q6_lost_revenue_by_store_category;
DROP VIEW IF EXISTS q7_rop_model_inputs;


-- --------------------------------------------------------------------------------------
-- Q1 — Stockout rate by store x category, ranked.
-- The headline diagnostic: where are we running dry, and is it concentrated?
-- --------------------------------------------------------------------------------------
CREATE VIEW q1_stockout_rate AS
SELECT
    i.store_id,
    s.category,
    COUNT(*)                                  AS n_store_days,
    SUM(i.stockout_flag)                      AS n_stockout_days,
    ROUND(AVG(i.stockout_flag) * 100, 2)      AS stockout_rate_pct,
    RANK() OVER (ORDER BY AVG(i.stockout_flag) DESC) AS stockout_rank
FROM inventory_snapshots i
JOIN skus s ON i.sku_id = s.sku_id
GROUP BY i.store_id, s.category
ORDER BY stockout_rate_pct DESC;


-- --------------------------------------------------------------------------------------
-- Q2 — Estimated revenue lost to stockouts.
--
-- Unmet demand is never logged, so it is inferred: on a stockout day, the units we would
-- have sold are proxied by the trailing 7-day mean of units_sold for that (store, sku),
-- and the shortfall vs what we actually sold is priced at unit_price.
--
-- The trailing window EXCLUDES the current day (ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
-- so the estimate is never contaminated by the stockout it is trying to measure. It is a
-- conservative floor: prior stockouts inside the window drag the baseline down.
-- --------------------------------------------------------------------------------------
CREATE VIEW q2_daily_with_rolling_avg AS
SELECT
    i.id,
    i.date,
    i.store_id,
    i.sku_id,
    i.units_sold,
    i.stockout_flag,
    s.category,
    s.unit_price,
    s.unit_cost,
    AVG(i.units_sold) OVER (
        PARTITION BY i.store_id, i.sku_id
        ORDER BY i.date
        ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
    ) AS rolling_avg_units_7d
FROM inventory_snapshots i
JOIN skus s ON i.sku_id = s.sku_id;

CREATE VIEW q2_lost_revenue_by_store AS
SELECT
    store_id,
    SUM(stockout_flag)                                       AS n_stockout_days,
    ROUND(SUM(
        CASE WHEN stockout_flag = 1 AND rolling_avg_units_7d > units_sold
             THEN (rolling_avg_units_7d - units_sold) * unit_price
             ELSE 0 END
    ), 2)                                                    AS estimated_lost_revenue,
    -- Same shortfall priced at margin, not revenue: the true P&L hit.
    ROUND(SUM(
        CASE WHEN stockout_flag = 1 AND rolling_avg_units_7d > units_sold
             THEN (rolling_avg_units_7d - units_sold) * (unit_price - unit_cost)
             ELSE 0 END
    ), 2)                                                    AS estimated_lost_margin
FROM q2_daily_with_rolling_avg
WHERE rolling_avg_units_7d IS NOT NULL
GROUP BY store_id
ORDER BY estimated_lost_revenue DESC;


-- --------------------------------------------------------------------------------------
-- Q3 — Weekend vs weekday demand delta by category.
-- strftime('%w'): 0=Sun ... 5=Fri, 6=Sat. Uses units_sold, so the measured lift is a
-- slight understatement wherever the weekend itself caused a stockout.
-- --------------------------------------------------------------------------------------
CREATE VIEW q3_weekend_demand_delta AS
SELECT
    s.category,
    ROUND(AVG(CASE WHEN strftime('%w', i.date) IN ('5','6') THEN i.units_sold END), 3)
        AS avg_weekend_units,
    ROUND(AVG(CASE WHEN strftime('%w', i.date) NOT IN ('5','6') THEN i.units_sold END), 3)
        AS avg_weekday_units,
    ROUND(
        (AVG(CASE WHEN strftime('%w', i.date) IN ('5','6') THEN i.units_sold END)
         / AVG(CASE WHEN strftime('%w', i.date) NOT IN ('5','6') THEN i.units_sold END)
         - 1) * 100, 1
    ) AS weekend_lift_pct
FROM inventory_snapshots i
JOIN skus s ON i.sku_id = s.sku_id
GROUP BY s.category
ORDER BY weekend_lift_pct DESC;


-- --------------------------------------------------------------------------------------
-- Q4 — Lead time vs stockout rate.
-- Under a zero-safety-stock reorder point, demand risk accumulates over the lead-time
-- window, so stockout rate should climb monotonically with lead_time_days. This is the
-- evidence that the driver is structural (replenishment timing), not demand noise.
-- --------------------------------------------------------------------------------------
CREATE VIEW q4_leadtime_vs_stockout AS
SELECT
    s.lead_time_days,
    ROUND(AVG(i.stockout_flag) * 100, 2) AS stockout_rate_pct,
    COUNT(*)                             AS n_observations,
    COUNT(DISTINCT s.sku_id)             AS n_skus
FROM inventory_snapshots i
JOIN skus s ON i.sku_id = s.sku_id
GROUP BY s.lead_time_days
ORDER BY s.lead_time_days;


-- --------------------------------------------------------------------------------------
-- Q5 — Root cause split: demand-driven vs supply-driven stockouts.
--
-- Logic: on a stockout day, did we run out because we sold unusually WELL (a demand
-- spike outran a reasonable stock position), or because there was barely anything on the
-- shelf to begin with (replenishment/ops failure)?
--
-- The baseline is a TRUE median of units_sold computed over non-stockout days only.
-- Using AVG over all days would be circular: stockout days are censored downward, so
-- they drag the very threshold they are then compared against.
-- --------------------------------------------------------------------------------------
CREATE VIEW q5_stockout_root_cause AS
WITH ranked AS (
    SELECT
        store_id, sku_id, units_sold,
        ROW_NUMBER() OVER (PARTITION BY store_id, sku_id ORDER BY units_sold) AS rn,
        COUNT(*)     OVER (PARTITION BY store_id, sku_id)                     AS cnt
    FROM inventory_snapshots
    WHERE stockout_flag = 0
),
store_sku_median AS (
    SELECT store_id, sku_id, AVG(units_sold) AS median_units
    FROM ranked
    WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)   -- true median, even- and odd-safe
    GROUP BY store_id, sku_id
),
classified AS (
    SELECT
        i.store_id,
        CASE WHEN i.units_sold > m.median_units THEN 'demand_driven'
             ELSE 'supply_driven' END AS root_cause
    FROM inventory_snapshots i
    JOIN store_sku_median m
      ON i.store_id = m.store_id AND i.sku_id = m.sku_id
    WHERE i.stockout_flag = 1
)
SELECT
    store_id,
    root_cause,
    COUNT(*) AS n_stockout_events,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY store_id), 1) AS pct_of_store_events
FROM classified
GROUP BY store_id, root_cause
ORDER BY store_id, n_stockout_events DESC;


-- --------------------------------------------------------------------------------------
-- Q6 (extension) — Lost revenue by store x category.
-- Pinpoints which cells of the store x category grid carry the money, so the Phase 3
-- policy can be targeted instead of blanket.
-- --------------------------------------------------------------------------------------
CREATE VIEW q6_lost_revenue_by_store_category AS
SELECT
    store_id,
    category,
    SUM(stockout_flag) AS n_stockout_days,
    ROUND(SUM(
        CASE WHEN stockout_flag = 1 AND rolling_avg_units_7d > units_sold
             THEN (rolling_avg_units_7d - units_sold) * unit_price
             ELSE 0 END
    ), 2) AS estimated_lost_revenue,
    ROUND(SUM(
        CASE WHEN stockout_flag = 1 AND rolling_avg_units_7d > units_sold
             THEN (rolling_avg_units_7d - units_sold) * (unit_price - unit_cost)
             ELSE 0 END
    ), 2) AS estimated_lost_margin
FROM q2_daily_with_rolling_avg
WHERE rolling_avg_units_7d IS NOT NULL
GROUP BY store_id, category
ORDER BY estimated_lost_revenue DESC;


-- --------------------------------------------------------------------------------------
-- Q7 (extension) — Reorder-point model inputs, per store x category.
--
-- Feeds the Excel ROP model:  ROP = (mu_d * L) + Z * sigma_d * SQRT(L)
-- Demand mean and sigma are measured over NON-stockout days only, because stockout days
-- are censored and would bias both downward. sigma_d is the population SD via the
-- E[x^2] - E[x]^2 identity (SQLite has no STDEV aggregate).
-- --------------------------------------------------------------------------------------
CREATE VIEW q7_rop_model_inputs AS
SELECT
    i.store_id,
    s.category,
    COUNT(DISTINCT i.sku_id)                              AS n_skus,
    ROUND(AVG(i.units_sold), 4)                           AS mu_daily_units_per_sku,
    ROUND(SQRT(MAX(AVG(i.units_sold * i.units_sold) - AVG(i.units_sold) * AVG(i.units_sold), 0)), 4)
                                                          AS sigma_daily_units_per_sku,
    ROUND(AVG(s.lead_time_days), 2)                       AS avg_lead_time_days,
    ROUND(AVG(s.unit_price), 2)                           AS avg_unit_price,
    ROUND(AVG(s.unit_cost), 2)                            AS avg_unit_cost,
    ROUND(AVG(s.unit_price - s.unit_cost), 2)             AS avg_unit_margin,
    ROUND(AVG(s.shelf_life_days), 1)                      AS avg_shelf_life_days
FROM inventory_snapshots i
JOIN skus s ON i.sku_id = s.sku_id
WHERE i.stockout_flag = 0
GROUP BY i.store_id, s.category
ORDER BY i.store_id, s.category;
