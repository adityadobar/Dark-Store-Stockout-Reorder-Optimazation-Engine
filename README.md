# Dark Store Reorder Optimization Model

**SQL · Excel (live model) · censored-demand estimation**

An end-to-end analysis of stockouts across 18 q-commerce dark stores: measure the revenue
leaking to empty shelves, separate the causes that look alike, and price the reorder-point
policy that recovers the most net margin.

> **Headline.** ₹16.4 lakh/month of revenue is lost to stockouts across 18 stores. The
> structural driver for dairy is **lead-time mismatch, not demand volatility** — reorder
> points are sized on a 3.29-day nominal lead time while goods take 4.33 days, so every
> cycle starts 3.19 units short. Re-sizing the reorder point for dairy at 3 stores returns
> **₹21,898/month in net margin**; the same change network-wide projects **₹3.39 lakh/month**.

---

## Deliverables

| Artefact | What it is |
|---|---|
| [`docs/recommendation_memo.md`](docs/recommendation_memo.md) | The one-page memo — findings, policy options, decision |
| [`excel/dark_store_reorder_model.xlsx`](excel/) | Live 11-sheet model: ROP maths, sensitivity grid, scenario summary, charts |
| [`sql/analysis_queries.sql`](sql/analysis_queries.sql) | 7 analysis views over the warehouse |
| [`docs/data_generation_notes.md`](docs/data_generation_notes.md) | Answer key for the synthetic data |
| `src/validate_estimates.py` | 8 automated checks — answer key + estimator accuracy |

---

## Run it

```bash
pip install -r requirements.txt
python src/run_all.py
```

~30 seconds end to end. Or run the stages individually:

```bash
python src/generate_data.py        # simulate 90 days x 18 stores x 180 SKUs
python src/load_data.py            # build db/dark_store.db
python src/run_queries.py          # create + export the 7 analysis views
python src/estimate_lost_revenue.py  # censored-demand lost revenue
python src/validate_estimates.py   # 8 checks — must pass before trusting anything
python src/rop_model.py            # policy economics + sensitivity
python src/build_workbook.py       # write the Excel model
```

---

## How it works

### 1. Data (`src/generate_data.py`)

A day-by-day inventory state machine — receive, shrink, sell, reorder — over 291,600
store-SKU-days. Every store runs a deliberately naive policy with **no safety stock**:

```
reorder_point  = mean_daily_demand × nominal_lead_time
order_quantity = mean_daily_demand × 7
```

Three patterns are injected as ground truth (documented in `docs/data_generation_notes.md`):
a dairy supplier delay at 3 stores, chronic shrinkage at 2 stores, and a Fri/Sat demand
lift on snacks and beverages.

### 2. Warehouse (`sql/`)

SQLite, four tables. **True demand is deliberately withheld** — `load_data.py` drops the
`units_demanded` column, because a real dark store never observes demand it cannot serve.
Every query works from censored data.

Seven views: stockout rates by store × category, lost revenue, weekend lift, lead-time
vs stockout rate, root-cause split, and two that feed the Excel model.

### 3. Measurement (`src/estimate_lost_revenue.py`)

The naive trailing-7-day method recovers only **39% of true lost revenue**. Two biases,
both measured against the withheld truth:

- Stockouts happen on **spike days** — demand on a stockout day averages 1.7x the trailing
  baseline, and a mean-anchored estimator cannot price the excess (27% recovery on partial
  stockouts).
- Days without a stockout are exactly the days demand was low, so the baseline is itself
  biased down.

The fix treats demand as right-censored: fit λ per store × SKU × weekend by **censored-
Poisson MLE**, then sum the expected shortage `E[(D−S)⁺]` over **every** store-day. Summing
only over realised stockout days conditions on the event being measured and costs ~45
points of coverage.

Result: **103% coverage** of ground truth, and the top-5 target stores recovered exactly.

### 4. Model (`src/rop_model.py`, `src/build_workbook.py`)

```
ROP = (μ_d × L_actual) + z × σ_DL        σ_DL = √(L·σ_d² + μ_d²·σ_LT²)
```

Lead times are **inferred from stock-arrival timing**, not assumed — the affected stores
measure +1.03 days versus peers on the same category.

The workbook is live formulas end to end, driven by named cells on `Inputs`. Verified by
evaluating the saved file with an independent formula engine: it reproduces the Python
model to the rupee.

---

## What the analysis had to get right

**The biggest loss is not the recommendation.** Stores 14 and 16 carry 34% of all lost
revenue and top every ranking — but their stockouts are elevated in *all six* categories
and 88% supply-driven, because stock is leaving without being sold. A reorder model
calibrated on them would forecast a recovery that raising the reorder point cannot deliver.
They are excluded from the rollout and routed to an ops audit.

**Lead-time variance is not the driver.** It contributes only 14.7% of σ_DL. The damage is
the lead-time **mean** being wrong — a bias, not noise — which is why the fix is re-sizing
the reorder point rather than merely buffering it.

**The economic optimum is not the recommendation.** The newsvendor critical ratio puts the
optimal service level at 99.1%, but that pushes dairy to 6.5 days of cover against a
6.7-day shelf life. Policy B (95%) captures 99% of the value with real headroom.

---

## Layout

```
├── data/raw/            generated CSVs (incl. withheld ground-truth demand)
├── data/processed/      query + model exports, q1 … q17
├── db/dark_store.db     SQLite warehouse
├── sql/                 schema.sql, analysis_queries.sql
├── src/                 generation, loading, estimation, model, workbook build
├── excel/               dark_store_reorder_model.xlsx
└── docs/                answer key, recommendation memo
```

Seeded with `RNG_SEED = 42` — fully reproducible.
