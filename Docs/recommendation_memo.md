# Recommendation: re-size dairy reorder points at three dark stores

**To:** VP Supply Chain · **From:** Analytics · **Date:** 11 Sep 2026
**Window analysed:** 1 Jan – 31 Mar 2026 · 18 dark stores · 180 SKUs · 291,600 store-days

---

## The ask

Approve a reorder-point change for **dairy at stores 3, 7 and 11**. Net impact
**₹21,898/month** on margin (₹63,997/month of recovered revenue). No headcount, no system
change, no capex — one parameter per SKU.

Two further items below need an owner, not an approval.

---

## What we found

Stockouts cost an estimated **₹16.4 lakh per month** in revenue (₹5.86 lakh in margin)
across the network, at a 5.4% store-day stockout rate. That number splits three ways, and
the split matters more than the total, because the three parts have different causes and
only one of them is a reorder-point problem.

| # | Root cause | Revenue lost / month | Share | Fix |
|---|---|---|---|---|
| **A** | Operational shrinkage — stores 14 & 16 | ₹5,57,019 | 33.9% | **Ops audit.** Not a planning issue |
| **B** | Lead-time mismatch — dairy @ 3, 7, 11 | ₹65,477 | 4.0% | **This memo** |
| **C** | No safety stock anywhere in the network | ₹10,18,344 | 62.1% | Phase 2 rollout |

**Bucket A is the trap.** Stores 14 and 16 top every stockout ranking and carry a third of
the loss, so they look like the obvious target. They are not. Their stockouts are elevated
in *all six* categories at once and 88% of them are supply-driven — stock is leaving these
buildings without being sold. Raising their reorder points would fund the leak, not close
it. **Recommend a physical inventory audit at both sites; explicitly exclude them from any
reorder-point change.**

---

## Why bucket B is a lead-time problem, not a demand problem

Dairy at stores 3, 7 and 11 runs a **12.9% stockout rate against a 4.5% peer baseline** —
2.9x, and confined to one category at three sites. Three tests separate the causes:

- **It is not weekend demand.** The Fri/Sat lift is real but sits entirely in snacks
  (+32.4%) and beverages (+32.5%). Dairy is flat at −0.6%.
- **It is not demand volatility.** Decomposing the spread of demand-over-lead-time,
  **85.3% comes from demand variability and only 14.7% from lead-time variability** — and
  the peer stores face the same demand variability without the same stockouts.
- **It is the lead time itself.** Measured from stock-arrival timing, these three stores
  wait **+1.03 days longer** for dairy than other stores do, while their reorder points are
  still sized on the nominal **3.29 days**. Goods actually take **4.33 days**.

That gap is a *bias*, not noise. Every cycle begins **3.19 units short** of the cover the
policy assumes. The reorder point is not too small for the variability — it is sized on the
wrong lead time.

> Current reorder point **10.20 units** · demand over actual lead time **13.39 units**
> Today's policy carries **negative** safety stock of −3.19 units. Fill rate: **87.3%**.

---

## The recommendation

Move dairy at stores 3, 7 and 11 to a **95% cycle service level**, with the reorder point
sized on the lead time actually delivered:

```
ROP = (mean daily demand × 4.33 days) + 1.64 × σ_DL       →  19.90 units   (from 10.20)
```

| Policy | Reorder point | Safety stock | Fill rate | Revenue recovered /mo | Holding /mo | **Net margin /mo** |
|---|---|---|---|---|---|---|
| Current | 10.20 u | −3.19 u | 87.3% | — | — | — |
| A — 90% | 18.47 u | +5.08 u | 99.4% | ₹62,124 | ₹681 | ₹21,352 |
| **B — 95%** | **19.90 u** | **+6.52 u** | **99.7%** | **₹63,997** | **₹799** | **₹21,898** |
| C — 99% | 22.60 u | +9.21 u | 99.9% | ₹65,237 | ₹1,021 | ₹22,116 |

**Why 95% and not 99%.** On pure economics 99% wins: dairy margin (₹22.15/unit) dwarfs the
holding cost of carrying a buffer (₹0.21/unit/cycle), which puts the newsvendor optimum at
**99.1%**. But 99% pushes average inventory to 20.0 units — **6.5 days of cover against a 6.7-day
dairy shelf life** — leaving a 3% margin before stock ages out. It is optimal and one bad
forecast from a write-off. Policy B captures **99.0% of the net value of Policy C** while leaving real headroom
against spoilage. The extra ₹218/month is not worth buying at the edge of the shelf life.

**The choice of service level is second-order anyway.** Across the 90–99% band the net
impact moves only 3.6%. Closing the lead-time gap is what produces the money.

---

## Two things this memo does not ask for

1. **Stores 14 & 16 → operations.** ₹5,57,019/month. Needs a physical audit and a cycle-
   count process, not a planning parameter.
2. **Network rollout → phase 2.** The same zero-safety-stock policy runs everywhere. Rolled
   out at 95% across the 16 non-shrinkage stores it projects **₹10,29,715/month of recovered
   revenue for ₹27,813/month of holding cost — ₹3,39,236/month net margin.** Stores 3, 7 and
   11 are the pilot: same mechanism, small blast radius, four weeks to a clean read.

---

## What would make this wrong

- **Lost revenue is estimated, not observed.** Unmet demand is never logged. The estimator
  treats stockout days as right-censored and is validated against withheld ground truth at
  **103% coverage**; a simpler trailing-average method recovers only 39% and would have
  understated the case by 2.5x. Direction and ranking are solid; treat the level as ±10%.
- **The +1.03-day lead-time gap is inferred** from stock-arrival timing, not from supplier
  records. It is a peer-relative measure and likely understates the true gap. **Confirm
  against supplier SLAs before renegotiating anything** — if the delay is contractual
  rather than operational, the cheaper fix is the contract, not the buffer.
- **Phase 2's ₹3.39L/month assumes** the pilot mechanism generalises. It should be re-based
  on pilot results, not booked now.

---

## Decision

☐ Approve Policy B for dairy at stores 3, 7, 11 — **₹21,898/month net margin**
☐ Open an ops audit at stores 14 & 16 — **₹5,57,019/month at stake**
☐ Note phase 2 network rollout — **₹3,39,236/month net margin**, to be re-based after pilot

*Model: `excel/dark_store_reorder_model.xlsx` · Analysis: `sql/analysis_queries.sql` ·
Validation: `src/validate_estimates.py` (8/8 passing)*
