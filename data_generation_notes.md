# Data Generation Notes — the answer key

This file records exactly what was injected into the synthetic dataset, so the SQL and
Excel analysis can be checked against ground truth. **Read this only after forming a view
from the data**, otherwise the analysis is circular.

`src/validate_estimates.py` turns every claim below into an automated assertion. All 8
checks pass on the committed dataset.

---

## Scale

| Parameter | Value |
|---|---|
| Dark stores | 18 |
| SKUs | 180 (30 in each of 6 categories) |
| Days | 90 (2026-01-01 → 2026-03-31) |
| Inventory snapshot rows | 291,600 |
| Order lines | ~417,000 |
| RNG seed | 42 (fully reproducible) |

---

## The underlying flaw: a naive reorder policy

Every store-SKU pair runs this policy:

```
reorder_point  = mean_daily_demand × nominal_lead_time_days      ← no safety stock
order_quantity = mean_daily_demand × 7                           ← one week of cover
```

The reorder point equals *mean* demand over the lead time, so by construction roughly
half of all replenishment cycles run dry before the truck lands. There is no buffer for
demand variability and no buffer for lead-time variability.

**This is the flaw the Phase 3 Excel model fixes.** It is deliberate.

---

## Injected pattern 1 — dairy supplier delay

**Stores 3, 7, 11. Dairy only.**

When one of these stores orders dairy, the delivery arrives *later* than the lead time
the reorder point was sized on:

```
effective_lead_time = nominal_lead_time + delay
delay = 1 day with p=0.6,  2 days with p=0.4     (mean +1.4 d, sd 0.49 d)
```

The reorder point is *not* updated to reflect this. The result is a structural mismatch:
the store reorders as if goods take ~3.3 days when they actually take ~4.7.

**What the analysis should recover.** Dairy stockout rate at these stores runs ~12.5–13.4%
against a ~4.5% peer baseline (2.9x). Crucially, the damage is a **mean shift, not extra
volatility** — lead-time variance contributes only ~15% of the demand-over-lead-time
spread. The correct diagnosis is "the reorder point is sized on the wrong number", and
the correct fix is re-sizing it, not merely buffering it.

---

## Injected pattern 2 — chronic operational shrinkage

**Stores 14, 16. All categories.**

Each day, independent of demand, stock is removed:

```
on_hand -= Poisson(1.5)      floored at 0
```

This simulates operational leakage — mispicks, damage, unrecorded write-offs, theft.
It is **not** a demand problem and **not** a replenishment problem.

**What the analysis should recover.** These stores show elevated stockouts in *all six*
categories (~14–21% vs ~3.8% peer), and their stockouts are ~88% "supply-driven" under
the Q5 classifier versus ~56% at clean stores.

**The trap this sets.** These two stores carry ~34% of all lost revenue and sit at the
top of every ranking. A reorder-point model calibrated on them would attribute their
losses to reorder policy and forecast a recovery that raising the reorder point cannot
deliver — it would simply fund more shrinkage. `src/rop_model.py` excludes them from the
rollout projection for exactly this reason, and the memo routes them to an ops audit.
Catching this is the main analytical test in the project.

---

## Injected pattern 3 — weekend demand lift

**Snacks and beverages only. Fridays and Saturdays.**

```
lambda_weekend = lambda_weekday × 1.35
```

The multiplier scales the Poisson **rate**, not the realised count. Scaling the count and
truncating back to an integer biases the uplift down by ~half a unit per day, which on a
mean of ~2 units/day shows up as a ~20% lift rather than the intended 35% — enough to
fail the validation gate for a reason that has nothing to do with the analysis.

**What the analysis should recover.** Snacks +32.4%, beverages +32.5%; every other
category within ±1%. The measured figure sits slightly below 35% because it is computed
on `units_sold`, which is censored on stockout days.

This pattern exists as a **control**: dairy is flat at −0.6%, which rules out weekend
demand as an explanation for the dairy stockouts.

---

## Emergent (not injected) — lead time vs stockout rate

Not injected, but a consequence of the zero-safety-stock policy: demand risk accumulates
over the lead-time window, so stockout rate rises monotonically with lead time.

| Lead time | Stockout rate |
|---|---|
| 1 day | 3.78% |
| 2 days | 5.72% |
| 3 days | 7.10% |
| 4 days | 8.20% |

This is the evidence that the driver is structural rather than demand noise.

---

## Withheld from the warehouse: true demand

`inventory_snapshots.csv` carries a `units_demanded` column. **`src/load_data.py` drops it
before loading into SQLite.**

A real dark store never observes demand it cannot serve — the customer bounces and nothing
is logged. Keeping true demand in the warehouse would make the lost-revenue analysis
trivial and unrealistic. Every query works from censored data; the withheld column is used
only in `validate_estimates.py` to score the estimators.

That scoring is what exposed the most important measurement issue in the project:

| Estimator | Recovers | Note |
|---|---|---|
| Naive trailing-7-day (SQL `q2`) | **39.3%** of true | Stockouts happen on above-average days; a mean-based baseline cannot price the spike |
| Censored-Poisson MLE (`q8`) | **103.1%** of true | Treats stockout days as right-censored; sums expected shortage over all days |

Both are reported. The naive figure is the defensible floor; the corrected figure is the
planning number.

---

## Ground truth summary

| Item | Value |
|---|---|
| `PROBLEM_STORES_DAIRY` | 3, 7, 11 |
| `CHRONIC_OPS_STORES` | 14, 16 |
| Weekend categories | snacks, beverages (Fri/Sat, ×1.35 on the rate) |
| Supplier delay | +1d (p=0.6) / +2d (p=0.4) |
| Shrinkage | Poisson(1.5) units/day/SKU |
| True lost revenue, 90 days | ₹4,775,722 |
| Top-5 loss stores (truth) | 16, 14, 7, 3, 11 — recovered exactly by `q8` |
