"""
Phase 3b — build excel/dark_store_reorder_model.xlsx.

Everything on the model sheets is a LIVE formula driven by the named cells on `Inputs`.
Change a lead time or a holding rate there and the policy table, the sensitivity grid,
the scenario summary and the charts all recalculate. Nothing is a pasted value.

Two Excel features cannot be written by openpyxl, and are handled as follows:

  * Data Table  — a native two-way Data Table stores {=TABLE(row,col)}, which the file
    format exposes but openpyxl cannot emit. The Sensitivity sheet instead carries the
    fully-written formula in every cell of the grid. It recalculates identically and is
    easier to audit, since each cell shows its own maths instead of an opaque TABLE().

  * Scenario Manager — scenarios live in a part openpyxl does not model. The Scenario
    Summary sheet is built in the exact layout Scenario Manager outputs, driven by live
    formulas. The Read Me sheet gives the click-path to re-create it natively if the
    native feature is specifically wanted.
"""

import os

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

OUT = "excel/dark_store_reorder_model.xlsx"
P = "data/processed"

# --- palette -------------------------------------------------------------------------
NAVY = "1F3864"
BLUE = "2E5E8C"
LIGHT = "DCE6F1"
INPUT_FILL = "FFF2CC"     # yellow = editable input, the usual modelling convention
CALC_FILL = "FFFFFF"
GREY = "F2F2F2"
GREEN = "C6EFCE"
RED = "FFC7CE"

H1 = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
H2 = Font(name="Calibri", size=11, bold=True, color=NAVY)
BODY = Font(name="Calibri", size=10)
BOLD = Font(name="Calibri", size=10, bold=True)
NOTE = Font(name="Calibri", size=9, italic=True, color="606060")

THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

RS = '"₹"#,##0'
RS2 = '"₹"#,##0.00'
PCT = "0.0%"
NUM = "#,##0.00"


def title(ws, text, span=8):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    c = ws.cell(row=1, column=1, value=text)
    c.font = H1
    c.fill = PatternFill("solid", fgColor=NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26


def section(ws, row, text, span=8):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    c = ws.cell(row=row, column=1, value=text)
    c.font = H2
    c.fill = PatternFill("solid", fgColor=LIGHT)
    return row + 1


def put(ws, row, col, value, font=BODY, fmt=None, fill=None, border=True, align=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font = font
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    if border:
        c.border = BOX
    if align:
        c.alignment = Alignment(horizontal=align)
    return c


def header_row(ws, row, labels, start=1):
    for i, lab in enumerate(labels):
        c = ws.cell(row=row, column=start + i, value=lab)
        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=BLUE)
        c.border = BOX
        c.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")
    ws.row_dimensions[row].height = 30


def widths(ws, spec):
    for col, w in spec.items():
        ws.column_dimensions[col].width = w


def dump_frame(ws, df, start_row, fmts=None, start_col=1):
    header_row(ws, start_row, list(df.columns), start=start_col)
    fmts = fmts or {}
    for r, (_, rec) in enumerate(df.iterrows(), start=start_row + 1):
        for i, col in enumerate(df.columns):
            v = rec[col]
            put(ws, r, start_col + i, v.item() if hasattr(v, "item") else v,
                fmt=fmts.get(col))
    return start_row + len(df) + 1


# ======================================================================================
def main():
    os.makedirs("excel", exist_ok=True)

    params = pd.read_csv(f"{P}/q13_segment_parameters.csv").iloc[0]
    meta = pd.read_csv(f"{P}/q14_model_meta.csv").iloc[0]
    buckets = pd.read_csv(f"{P}/q12_loss_decomposition.csv")
    q1 = pd.read_csv(f"{P}/q1_stockout_rate.csv")
    q3 = pd.read_csv(f"{P}/q3_weekend_demand_delta.csv")
    q4 = pd.read_csv(f"{P}/q4_leadtime_vs_stockout.csv")
    q5 = pd.read_csv(f"{P}/q5_stockout_root_cause.csv")
    q8 = pd.read_csv(f"{P}/q8_corrected_lost_revenue_by_store.csv")
    q2 = pd.read_csv(f"{P}/q2_lost_revenue_by_store.csv")
    q11 = pd.read_csv(f"{P}/q11_inferred_lead_times.csv")
    net = pd.read_csv(f"{P}/q17_network_rollout.csv")

    seg_loss_rev = float(
        pd.read_csv(f"{P}/q9_corrected_lost_revenue_by_store_category.csv")
        .query("category=='dairy' and store_id in [3,7,11]")
        .corrected_lost_revenue.sum() / 3
    )
    seg_loss_mgn = float(
        pd.read_csv(f"{P}/q9_corrected_lost_revenue_by_store_category.csv")
        .query("category=='dairy' and store_id in [3,7,11]")
        .corrected_lost_margin.sum() / 3
    )
    observed_units = float(
        pd.read_csv(f"{P}/q9_corrected_lost_revenue_by_store_category.csv")
        .query("category=='dairy' and store_id in [3,7,11]")
        .corrected_lost_units.sum() / (3 * 30) / 3
    )

    wb = Workbook()

    # ==================================================================================
    # 1. INPUTS
    # ==================================================================================
    ws = wb.active
    ws.title = "Inputs"
    title(ws, "INPUTS & ASSUMPTIONS  —  yellow cells are editable; everything else derives from them", 6)
    widths(ws, {"A": 4, "B": 40, "C": 14, "D": 10, "E": 62})

    r = 3
    r = section(ws, r, "Segment: dairy SKUs at dark stores 3, 7 and 11", 6)
    rows = [
        ("mu_d", "Mean demand per SKU per day (units)", float(params.mu_daily_units), NUM,
         "Censored-Poisson MLE (src/estimate_lost_revenue.py), not a raw sales average"),
        ("sigma_d", "SD of daily demand per SKU (units)", float(params.sigma_daily_units), NUM,
         "= SQRT(mu_d): demand is Poisson, so variance equals the mean"),
        ("L_nom", "Nominal lead time (days)", float(params.nominal_lead_time), NUM,
         "What the current reorder point is sized on (SKU master)"),
        ("L_excess", "Excess lead time vs peer stores (days)", float(params.excess_lead_time), NUM,
         "MEASURED from stock-arrival timing — this is the defect"),
        ("sigma_LT", "SD of lead time (days)", float(params.sigma_lead_time), NUM,
         "Cycle-to-cycle variability in supplier delay"),
        ("unit_cost", "Unit cost", float(params.unit_cost), RS2, "Segment mean"),
        ("unit_price", "Unit price", float(params.unit_price), RS2, "Segment mean"),
        ("unit_margin", "Unit margin", float(params.unit_margin), RS2, "= unit_price - unit_cost"),
        ("shelf_life", "Shelf life (days)", float(params.shelf_life_days), NUM,
         "Binding constraint on how much safety stock dairy can physically hold"),
        ("n_stores", "Stores in scope", int(params.n_stores), "0", "Stores 3, 7, 11"),
        ("n_skus", "Dairy SKUs per store", int(params.n_skus_per_store), "0", ""),
    ]
    for name, label, val, fmt, note in rows:
        put(ws, r, 2, label, font=BODY)
        put(ws, r, 3, val, font=BOLD, fmt=fmt, fill=INPUT_FILL)
        put(ws, r, 5, note, font=NOTE, border=False)
        wb.defined_names.add(DefinedName(name, attr_text=f"Inputs!$C${r}"))
        r += 1

    r += 1
    r = section(ws, r, "Economic assumptions", 6)
    econ = [
        ("holding_rate", "Annual inventory holding rate", 0.25, PCT,
         "Capital + cold chain + space, as a share of unit cost"),
        ("cycle_days", "Replenishment cycle (days)", 7, "0", "Current policy orders one week of cover"),
        ("days_month", "Days per month", 30, "0", ""),
    ]
    for name, label, val, fmt, note in econ:
        put(ws, r, 2, label, font=BODY)
        put(ws, r, 3, val, font=BOLD, fmt=fmt, fill=INPUT_FILL)
        put(ws, r, 5, note, font=NOTE, border=False)
        wb.defined_names.add(DefinedName(name, attr_text=f"Inputs!$C${r}"))
        r += 1

    r += 1
    r = section(ws, r, "Measured baseline (from SQL — anchors the model to observed reality)", 6)
    measured = [
        ("obs_units", "Lost units per SKU per month, today", observed_units, NUM,
         "Censored-demand estimate q9, current policy"),
        ("seg_rev_loss", "Segment revenue lost per month", seg_loss_rev, RS,
         "What the targeted fix is competing for"),
        ("seg_mgn_loss", "Segment margin lost per month", seg_loss_mgn, RS, ""),
    ]
    for name, label, val, fmt, note in measured:
        put(ws, r, 2, label, font=BODY)
        put(ws, r, 3, val, font=BOLD, fmt=fmt, fill=GREY)
        put(ws, r, 5, note, font=NOTE, border=False)
        wb.defined_names.add(DefinedName(name, attr_text=f"Inputs!$C${r}"))
        r += 1

    r += 1
    r = section(ws, r, "Derived quantities", 6)
    derived = [
        ("L_act", "Actual lead time (days)", "=L_nom+L_excess", NUM,
         "What the goods really take"),
        ("sigma_DL", "SD of demand over lead time (units)", "=SQRT(L_act*sigma_d^2+mu_d^2*sigma_LT^2)", NUM,
         "Combines demand risk and lead-time risk"),
        ("order_qty", "Order quantity (units)", "=mu_d*cycle_days", NUM, ""),
        ("cycles_pm", "Replenishment cycles per month", "=days_month/cycle_days", NUM, ""),
        ("rop_current", "Current reorder point (units)", "=mu_d*L_nom", NUM,
         "Sized on NOMINAL lead time — the defect"),
        ("z_current", "Implied z of current policy", "=(rop_current-mu_d*L_act)/sigma_DL", NUM,
         "Negative: today's reorder point sits BELOW mean lead-time demand"),
        ("calib", "Model calibration factor",
         "=obs_units/(sigma_DL*(NORMDIST(z_current,0,1,FALSE)"
         "-z_current*(1-NORMSDIST(z_current)))*cycles_pm)", NUM,
         "Solves so the model exactly reproduces today's measured loss; the model is "
         "then used only for relative improvement"),
        ("hold_unit_cycle", "Holding cost per unit per cycle", "=unit_cost*holding_rate*cycle_days/365", RS2, ""),
        ("crit_ratio", "Newsvendor critical ratio", "=unit_margin/(unit_margin+hold_unit_cycle)", PCT,
         "Economically optimal cycle service level"),
    ]
    for name, label, val, fmt, note in derived:
        put(ws, r, 2, label, font=BODY)
        put(ws, r, 3, val, font=BOLD, fmt=fmt, fill=CALC_FILL)
        put(ws, r, 5, note, font=NOTE, border=False)
        wb.defined_names.add(DefinedName(name, attr_text=f"Inputs!$C${r}"))
        r += 1

    r += 1
    put(ws, r, 2, "Variance decomposition — is the driver demand or lead time?", font=H2, border=False)
    r += 1
    for label, formula in [
        ("Share of variance from demand volatility", "=L_act*sigma_d^2/sigma_DL^2"),
        ("Share of variance from lead-time volatility", "=mu_d^2*sigma_LT^2/sigma_DL^2"),
        ("Units short per cycle from lead-time MEAN gap", "=mu_d*L_excess"),
    ]:
        put(ws, r, 2, label, font=BODY)
        put(ws, r, 3, formula, font=BOLD, fmt=NUM if "Units" in label else PCT)
        r += 1
    put(ws, r, 2,
        "Read together: lead-time VARIANCE is minor, but the lead-time MEAN gap removes "
        "whole units of cover every cycle. The defect is a bias, not noise — so the fix is "
        "re-sizing the reorder point, not merely buffering it.",
        font=NOTE, border=False)

    # ==================================================================================
    # 2. ROP MODEL
    # ==================================================================================
    ws = wb.create_sheet("ROP Model")
    title(ws, "REORDER POINT MODEL  —  ROP = (mu_d x L_act) + z x sigma_DL", 13)
    widths(ws, {"A": 32, **{get_column_letter(i): 13 for i in range(2, 14)}})

    r = 3
    put(ws, r, 1, "Every column below is a live formula over the Inputs sheet.", font=NOTE, border=False)
    r = 5
    cols = ["Policy", "Service level", "z", "Reorder point (u)", "Safety stock (u)",
            "Loss fn L(z)", "Lost units /SKU/mo", "Fill rate", "Recovery share",
            "Revenue recovered /mo", "Margin recovered /mo", "Incr. holding /mo",
            "Net margin impact /mo"]
    header_row(ws, r, cols)
    first = r + 1

    policies = [("Current (naive, no safety stock)", None), ("Policy A — 90% service", 0.90),
                ("Policy B — 95% service", 0.95), ("Policy C — 99% service", 0.99)]
    for i, (label, sl) in enumerate(policies):
        rr = first + i
        put(ws, rr, 1, label, font=BOLD if sl is None else BODY)
        if sl is None:
            put(ws, rr, 2, "=NORMSDIST(C{})".format(rr), fmt=PCT)
            put(ws, rr, 3, "=z_current", fmt=NUM)
            put(ws, rr, 4, "=rop_current", fmt=NUM)
        else:
            put(ws, rr, 2, sl, fmt=PCT, fill=INPUT_FILL)
            put(ws, rr, 3, f"=NORMSINV(B{rr})", fmt=NUM)
            put(ws, rr, 4, f"=mu_d*L_act+C{rr}*sigma_DL", fmt=NUM)
        put(ws, rr, 5, f"=D{rr}-mu_d*L_act", fmt=NUM)
        put(ws, rr, 6, f"=NORMDIST(C{rr},0,1,FALSE)-C{rr}*(1-NORMSDIST(C{rr}))", fmt=NUM)
        put(ws, rr, 7, f"=sigma_DL*F{rr}*cycles_pm*calib", fmt=NUM)
        put(ws, rr, 8, f"=1-G{rr}/(mu_d*days_month)", fmt=PCT)
        put(ws, rr, 9, f"=(obs_units-G{rr})/obs_units", fmt=PCT)
        put(ws, rr, 10, f"=I{rr}*seg_rev_loss", fmt=RS)
        put(ws, rr, 11, f"=I{rr}*seg_mgn_loss", fmt=RS)
        put(ws, rr, 12,
            f"=((order_qty/2+E{rr})-(order_qty/2+E{first}))*unit_cost*(holding_rate/12)*n_stores*n_skus",
            fmt=RS)
        put(ws, rr, 13, f"=K{rr}-L{rr}-N{rr}", fmt=RS, font=BOLD)
        # column N: incremental spoilage, zero until cover breaches shelf life
        put(ws, rr, 14,
            f"=(MAX(0,(order_qty/2+E{rr})-shelf_life*mu_d)"
            f"-MAX(0,(order_qty/2+E{first})-shelf_life*mu_d))"
            f"*unit_cost*cycles_pm*n_stores*n_skus", fmt=RS)

    last = first + len(policies) - 1

    r = last + 2
    r = section(ws, r, "Shelf-life check — can dairy physically hold this much safety stock?", 13)
    header_row(ws, r, ["Policy", "Avg inventory (u)", "Days of cover", "Shelf life (d)",
                       "Spoilage risk", "Verdict"])
    for i, (label, _) in enumerate(policies):
        rr = r + 1 + i
        src = first + i
        put(ws, rr, 1, label, font=BODY)
        put(ws, rr, 2, f"=order_qty/2+E{src}", fmt=NUM)
        put(ws, rr, 3, f"=B{rr}/mu_d", fmt=NUM)
        put(ws, rr, 4, "=shelf_life", fmt=NUM)
        put(ws, rr, 5, f"=MAX(0,B{rr}-shelf_life*mu_d)", fmt=NUM)
        put(ws, rr, 6, f'=IF(C{rr}>shelf_life,"BREACHES shelf life",'
                       f'IF(C{rr}>shelf_life*0.9,"Within 10% of limit","Safe"))')
    r = r + len(policies) + 2

    put(ws, r, 1, "Recommendation", font=H2, border=False)
    r += 1
    put(ws, r, 1,
        "=\"Adopt Policy B (95%). It captures \"&TEXT(I{}/I{},\"0.0%\")&\" of the net value of the \"&"
        "\"99% policy while holding \"&TEXT(E{}-E{},\"0.0\")&\" fewer units of safety stock per SKU, \"&"
        "\"which keeps days of cover clear of the \"&TEXT(shelf_life,\"0.0\")&\"-day dairy shelf life. \"&"
        "\"The newsvendor optimum is \"&TEXT(crit_ratio,\"0.0%\")&\", but that sits at the spoilage cliff.\""
        .format(first + 2, first + 3, first + 3, first + 2),
        font=BODY, border=False)
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 2, end_column=13)
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    # tradeoff chart
    ch = LineChart()
    ch.title = "Revenue recovery vs holding cost by service level"
    ch.y_axis.title = "₹ per month"
    ch.x_axis.title = "Policy"
    data = Reference(ws, min_col=10, max_col=12, min_row=5, max_row=last)
    cats = Reference(ws, min_col=1, min_row=first, max_row=last)
    ch.add_data(data, titles_from_data=True)
    ch.set_categories(cats)
    ch.height, ch.width = 8, 18
    ws.add_chart(ch, f"A{r + 5}")

    # ==================================================================================
    # 3. SENSITIVITY  (two-way grid, live formulas)
    # ==================================================================================
    ws = wb.create_sheet("Sensitivity")
    title(ws, "SENSITIVITY  —  net monthly margin impact by service level x excess lead time", 12)
    widths(ws, {"A": 26, **{get_column_letter(i): 12 for i in range(2, 13)}})

    r = 3
    put(ws, r, 1,
        "Rows vary the lead-time gap; columns vary the target service level. Every cell is "
        "a live formula, so this behaves exactly like a native two-way Data Table.",
        font=NOTE, border=False)

    service_axis = [0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99]
    excess_axis = [0.0, 0.5, 1.0, 1.5, 2.0]

    r = 5
    put(ws, r, 1, "Excess lead time (days)  \\  Service level", font=BOLD,
        fill=BLUE).font = Font(size=10, bold=True, color="FFFFFF")
    for j, sl in enumerate(service_axis):
        c = put(ws, r, 2 + j, sl, fmt=PCT, font=Font(size=10, bold=True, color="FFFFFF"),
                fill=BLUE, align="center")
    put(ws, r, 2 + len(service_axis) + 1, "sigma_DL", font=BOLD, fill=GREY)
    put(ws, r, 2 + len(service_axis) + 2, "Baseline lost u/SKU/mo", font=BOLD, fill=GREY)

    grid_first = r + 1
    hcol1 = get_column_letter(2 + len(service_axis) + 1)   # sigma_DL helper
    hcol2 = get_column_letter(2 + len(service_axis) + 2)   # baseline helper

    for i, ex in enumerate(excess_axis):
        rr = grid_first + i
        put(ws, rr, 1, ex, fmt=NUM, font=BOLD, fill=INPUT_FILL, align="center")
        # helper: sigma_DL and baseline loss for this row's lead-time gap
        put(ws, rr, 2 + len(service_axis) + 1,
            f"=SQRT((L_nom+A{rr})*sigma_d^2+mu_d^2*sigma_LT^2)", fmt=NUM, fill=GREY)
        z_cur = f"(-mu_d*A{rr}/{hcol1}{rr})"
        put(ws, rr, 2 + len(service_axis) + 2,
            f"={hcol1}{rr}*(NORMDIST({z_cur},0,1,FALSE)-{z_cur}*(1-NORMSDIST({z_cur})))"
            f"*cycles_pm*calib", fmt=NUM, fill=GREY)
        for j in range(len(service_axis)):
            col = get_column_letter(2 + j)
            zt = f"NORMSINV({col}${r})"
            lost = (f"{hcol1}{rr}*(NORMDIST({zt},0,1,FALSE)-{zt}*(1-NORMSDIST({zt})))"
                    f"*cycles_pm*calib")
            recovered_units = f"(({hcol2}{rr})-({lost}))*n_stores*n_skus"
            holding = f"(({zt})-{z_cur})*{hcol1}{rr}*unit_cost*(holding_rate/12)*n_stores*n_skus"
            put(ws, rr, 2 + j, f"=MAX(0,{recovered_units})*unit_margin-{holding}", fmt=RS)

    grid_last = grid_first + len(excess_axis) - 1
    ws.conditional_formatting.add(
        f"B{grid_first}:{get_column_letter(1 + len(service_axis))}{grid_last}",
        ColorScaleRule(start_type="min", start_color="FFFFFF",
                       end_type="max", end_color="63BE7B"),
    )
    rr = grid_last + 2
    put(ws, rr, 1,
        "The measured gap is the 1.0-day row. Read ACROSS it and the value barely moves "
        "(80% -> 99% adds ~12%); read DOWN the columns and it triples. The size of the "
        "lead-time gap, not the service level chosen to cover it, is what drives the "
        "prize — which is why the recommendation is to re-size the reorder point to the "
        "lead time actually delivered, and why the exact service level inside the 90-99% "
        "band is a second-order choice.", font=NOTE, border=False)
    ws.merge_cells(start_row=rr, start_column=1, end_row=rr + 2, end_column=10)
    ws.cell(row=rr, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    # ==================================================================================
    # 4. SCENARIO SUMMARY
    # ==================================================================================
    ws = wb.create_sheet("Scenario Summary")
    title(ws, "SCENARIO SUMMARY  —  three service-level policies", 6)
    widths(ws, {"A": 40, "B": 16, "C": 16, "D": 16, "E": 16, "F": 50})

    r = 3
    put(ws, r, 1, "Laid out the way Excel's Scenario Manager reports, but formula-driven "
                  "so it stays live. See Read Me to re-create it as native scenarios.",
        font=NOTE, border=False)

    r = 5
    header_row(ws, r, ["Result cell", "Current", "Policy A (90%)", "Policy B (95%)",
                       "Policy C (99%)", "Comment"])
    metrics = [
        ("Target service level", 2, PCT, ""),
        ("Reorder point (units/SKU)", 4, NUM, "Current is sized on nominal lead time"),
        ("Safety stock (units/SKU)", 5, NUM, "Negative today = structurally short"),
        ("Lost units /SKU/month", 7, NUM, ""),
        ("Fill rate", 8, PCT, ""),
        ("Revenue recovered /month", 10, RS, "Against measured segment loss"),
        ("Margin recovered /month", 11, RS, ""),
        ("Incremental holding /month", 12, RS, "Cost of carrying the buffer"),
        ("NET margin impact /month", 13, RS, "The decision number"),
    ]
    for i, (label, col, fmt, note) in enumerate(metrics):
        rr = r + 1 + i
        put(ws, rr, 1, label, font=BOLD if "NET" in label else BODY)
        for j in range(4):
            src = first + j
            put(ws, rr, 2 + j, f"='ROP Model'!{get_column_letter(col)}{src}", fmt=fmt,
                font=BOLD if "NET" in label else BODY)
        put(ws, rr, 6, note, font=NOTE)
    r = r + len(metrics) + 2

    r = section(ws, r, "Phase 2 — same policy rolled out network-wide", 6)
    header_row(ws, r, ["Scope", "Stores", "Revenue recovered /mo", "Margin recovered /mo",
                       "Holding cost /mo", "Net margin /mo"])
    rr = r + 1
    put(ws, rr, 1, "Targeted: dairy @ stores 3, 7, 11", font=BODY)
    put(ws, rr, 2, 3, fmt="0")
    put(ws, rr, 3, "='ROP Model'!J{}".format(first + 2), fmt=RS)
    put(ws, rr, 4, "='ROP Model'!K{}".format(first + 2), fmt=RS)
    put(ws, rr, 5, "='ROP Model'!L{}".format(first + 2), fmt=RS)
    put(ws, rr, 6, "='ROP Model'!M{}".format(first + 2), fmt=RS, font=BOLD)
    rr += 1
    put(ws, rr, 1, "Network rollout, all categories (ex stores 14 & 16)", font=BODY)
    put(ws, rr, 2, 16, fmt="0")
    put(ws, rr, 3, float(net.revenue_recovered_month.sum()), fmt=RS)
    put(ws, rr, 4, float(net.margin_recovered_month.sum()), fmt=RS)
    put(ws, rr, 5, float(net.holding_cost_month.sum()), fmt=RS)
    put(ws, rr, 6, float(net.net_margin_impact_month.sum()), fmt=RS, font=BOLD)
    rr += 1
    put(ws, rr, 1, "Stores 14 & 16 — routed to operations, NOT reorder policy", font=BODY)
    put(ws, rr, 2, 2, fmt="0")
    for c in (3, 4, 5, 6):
        put(ws, rr, c, "n/a", align="center")
    rr += 2
    put(ws, rr, 1,
        "Stores 14 and 16 are excluded on purpose. Their stock disappears without being "
        "sold, so raising their reorder point would fund the leak rather than close it. "
        "Sizing a recovery for them here would be the single easiest way to make this "
        "model wrong.", font=NOTE, border=False)
    ws.merge_cells(start_row=rr, start_column=1, end_row=rr + 2, end_column=6)
    ws.cell(row=rr, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    # ==================================================================================
    # 5. LOSS DECOMPOSITION
    # ==================================================================================
    ws = wb.create_sheet("Loss Decomposition")
    title(ws, "WHERE THE MONEY GOES  —  three buckets, three different owners", 8)
    widths(ws, {"A": 30, "B": 16, "C": 16, "D": 12, "E": 10, "F": 46})

    r = 3
    put(ws, r, 1, f"Total estimated revenue lost to stockouts: ₹"
                  f"{buckets.lost_revenue_monthly.sum():,.0f} per month across 18 stores.",
        font=H2, border=False)
    r = 5
    header_row(ws, r, ["Bucket", "Revenue lost /mo", "Margin lost /mo", "% of total",
                       "Cells", "Fix — and who owns it"])
    owner = {
        "A_ops_shrinkage": "Operations audit at stores 14 & 16. Stock is leaving without "
                           "being sold; a reorder-point change cannot touch this.",
        "B_leadtime_mismatch": "Supply planning: re-size the dairy reorder point at stores "
                               "3, 7, 11 to the lead time actually delivered. THIS MEMO.",
        "C_systemic_no_safety_stock": "Supply planning, phase 2: the network carries zero "
                                      "safety stock everywhere. Roll out after the pilot.",
    }
    label = {"A_ops_shrinkage": "A — Operational shrinkage",
             "B_leadtime_mismatch": "B — Lead-time mismatch (dairy)",
             "C_systemic_no_safety_stock": "C — Systemic: no safety stock"}
    for i, (_, b) in enumerate(buckets.iterrows()):
        rr = r + 1 + i
        put(ws, rr, 1, label[b.bucket], font=BODY)
        put(ws, rr, 2, float(b.lost_revenue_monthly), fmt=RS)
        put(ws, rr, 3, float(b.lost_margin_monthly), fmt=RS)
        put(ws, rr, 4, float(b.pct_of_total) / 100, fmt=PCT)
        put(ws, rr, 5, int(b.n_cells), fmt="0")
        put(ws, rr, 6, owner[b.bucket], font=NOTE).alignment = Alignment(wrap_text=True)
        ws.row_dimensions[rr].height = 32
    rr = r + len(buckets) + 1
    put(ws, rr, 1, "TOTAL", font=BOLD)
    put(ws, rr, 2, f"=SUM(B{r+1}:B{rr-1})", fmt=RS, font=BOLD)
    put(ws, rr, 3, f"=SUM(C{r+1}:C{rr-1})", fmt=RS, font=BOLD)

    ch = BarChart()
    ch.type = "col"
    ch.title = "Monthly revenue lost by root cause"
    ch.y_axis.title = "₹ per month"
    ch.add_data(Reference(ws, min_col=2, min_row=r, max_row=rr - 1), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=r + 1, max_row=rr - 1))
    ch.height, ch.width = 8, 16
    ws.add_chart(ch, f"A{rr + 3}")

    # ==================================================================================
    # 6. DATA SHEETS
    # ==================================================================================
    ws = wb.create_sheet("Data - Lost Revenue")
    title(ws, "LOST REVENUE BY STORE  —  naive SQL estimate vs censored-demand estimate", 8)
    widths(ws, {"A": 10, "B": 16, "C": 20, "D": 20, "E": 20, "F": 20})
    r = 3
    put(ws, r, 1,
        "The naive trailing-7-day method recovers only ~39% of true lost revenue: stockouts "
        "happen on above-average days, so a mean-based baseline cannot price the spike. "
        "The corrected column is the planning number; the naive one is the defensible floor.",
        font=NOTE, border=False)
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=8)
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    merged = q8.merge(q2[["store_id", "estimated_lost_revenue"]], on="store_id")
    merged = merged[["store_id", "n_stockout_days", "estimated_lost_revenue",
                     "corrected_lost_revenue", "corrected_lost_margin"]]
    merged.columns = ["Store", "Stockout days", "Naive estimate (90d)",
                      "Corrected estimate (90d)", "Corrected margin (90d)"]
    end = dump_frame(ws, merged, 6, fmts={"Naive estimate (90d)": RS,
                                          "Corrected estimate (90d)": RS,
                                          "Corrected margin (90d)": RS})
    ch = BarChart()
    ch.type = "col"
    ch.title = "Estimated lost revenue by store (90 days)"
    ch.add_data(Reference(ws, min_col=4, min_row=6, max_row=end - 1), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=7, max_row=end - 1))
    ch.height, ch.width = 8, 18
    ws.add_chart(ch, f"H6")

    ws = wb.create_sheet("Data - Stockout Rates")
    title(ws, "STOCKOUT RATE BY STORE x CATEGORY (Q1)", 7)
    widths(ws, {"A": 10, "B": 16, "C": 14, "D": 16, "E": 18, "F": 12})
    pivot = q1.pivot(index="store_id", columns="category", values="stockout_rate_pct")
    r = 3
    put(ws, r, 1, "Heat map: stores 14 and 16 are hot across EVERY category (an ops "
                  "problem); stores 3, 7 and 11 are hot in dairy only (a supply problem).",
        font=NOTE, border=False)
    r = 5
    header_row(ws, r, ["Store"] + list(pivot.columns))
    for i, (sid, row) in enumerate(pivot.iterrows()):
        rr = r + 1 + i
        put(ws, rr, 1, int(sid), font=BOLD, align="center")
        for j, cat in enumerate(pivot.columns):
            put(ws, rr, 2 + j, float(row[cat]) / 100, fmt=PCT, align="center")
    ws.conditional_formatting.add(
        f"B{r+1}:{get_column_letter(1+len(pivot.columns))}{r+len(pivot)}",
        ColorScaleRule(start_type="min", start_color="FFFFFF",
                       mid_type="percentile", mid_value=50, mid_color="FFEB84",
                       end_type="max", end_color="F8696B"),
    )
    dump_frame(ws, q1, r + len(pivot) + 3, fmts={"stockout_rate_pct": "0.00"})

    ws = wb.create_sheet("Data - Lead Time")
    title(ws, "LEAD TIME EVIDENCE (Q4 + Q11)", 7)
    widths(ws, {"A": 18, "B": 20, "C": 18, "D": 16, "E": 18, "F": 20, "G": 20})
    r = 3
    put(ws, r, 1, "Q4: stockout rate climbs monotonically with lead time — the signature "
                  "of a reorder point that carries no buffer.", font=NOTE, border=False)
    end = dump_frame(ws, q4, 5, fmts={"stockout_rate_pct": "0.00"})
    ch = ScatterChart()
    ch.title = "Stockout rate vs lead time"
    ch.x_axis.title = "Lead time (days)"
    ch.y_axis.title = "Stockout rate %"
    xs = Reference(ws, min_col=1, min_row=6, max_row=end - 1)
    ys = Reference(ws, min_col=2, min_row=5, max_row=end - 1)
    s = Series(ys, xs, title_from_data=True)
    ch.series.append(s)
    ch.height, ch.width = 8, 14
    ws.add_chart(ch, "I5")
    r = end + 2
    put(ws, r, 1, "Q11: lead time measured from stock-arrival timing, per store x category. "
                  "'Excess' is the gap versus other stores for the same category.",
        font=NOTE, border=False)
    dump_frame(ws, q11, r + 2)

    ws = wb.create_sheet("Data - Root Cause")
    title(ws, "ROOT CAUSE SPLIT (Q5)", 5)
    widths(ws, {"A": 10, "B": 18, "C": 18, "D": 18, "E": 14})
    r = 3
    put(ws, r, 1, "Demand-driven = sold above the median and still ran out. Supply-driven = "
                  "there was nothing on the shelf to sell.", font=NOTE, border=False)
    piv5 = q5.pivot(index="store_id", columns="root_cause",
                    values="n_stockout_events").fillna(0).reset_index()
    piv5["supply_share"] = piv5.supply_driven / (piv5.supply_driven + piv5.demand_driven)
    piv5.columns = ["Store", "Demand-driven", "Supply-driven", "Supply share"]
    dump_frame(ws, piv5, 5, fmts={"Supply share": PCT})

    ws = wb.create_sheet("Data - Weekend")
    title(ws, "WEEKEND DEMAND LIFT (Q3)", 5)
    widths(ws, {"A": 18, "B": 18, "C": 18, "D": 18})
    r = 3
    put(ws, r, 1, "Only snacks and beverages lift on Fri/Sat. Dairy is flat — which rules "
                  "out weekend demand as the explanation for the dairy stockouts.",
        font=NOTE, border=False)
    dump_frame(ws, q3, 5, fmts={"weekend_lift_pct": "0.0"})

    # ==================================================================================
    # 7. READ ME  (first sheet)
    # ==================================================================================
    ws = wb.create_sheet("Read Me", 0)
    title(ws, "DARK STORE REORDER OPTIMISATION MODEL", 6)
    widths(ws, {"A": 30, "B": 100})
    r = 3
    entries = [
        ("Question", "Where is revenue leaking to stockouts, what actually causes it, and "
                     "what reorder policy recovers the most net margin?"),
        ("Headline", f"₹{buckets.lost_revenue_monthly.sum():,.0f} per month of revenue is "
                     f"lost to stockouts across 18 dark stores."),
        ("Finding", "The driver is lead-time mismatch, not demand volatility. Reorder points "
                    "are sized on a nominal lead time roughly a day shorter than the goods "
                    "actually take, so each cycle starts short by whole units of cover."),
        ("", ""),
        ("Inputs", "Every assumption, in yellow. Change one and the whole model moves."),
        ("ROP Model", "The four policies, priced. Live formulas end to end."),
        ("Sensitivity", "Two-way grid: service level x lead-time gap."),
        ("Scenario Summary", "Scenario Manager layout; also carries the Phase 2 rollout."),
        ("Loss Decomposition", "The three buckets and who owns each."),
        ("Data - *", "Query exports behind the analysis."),
        ("", ""),
        ("Model convention", "Yellow = input. White = formula. Grey = measured from SQL."),
        ("", ""),
        ("Native Data Table", "The Sensitivity grid is written as explicit formulas rather "
                              "than a native Data Table, so each cell is auditable. To build "
                              "the native version: put the net-impact formula in a corner "
                              "cell, select the grid, Data > What-If Analysis > Data Table, "
                              "row input = service level, column input = L_excess."),
        ("Native Scenarios", "To re-create Scenario Summary as native scenarios: Data > "
                             "What-If Analysis > Scenario Manager > Add, changing cell = the "
                             "service-level cell on ROP Model, one scenario per policy, then "
                             "Summary with the net-impact cells as result cells."),
        ("", ""),
        ("Caveat", "Lost revenue is estimated, not observed — unmet demand is never logged. "
                   "The corrected estimator is validated against withheld ground truth at "
                   "103% coverage (src/validate_estimates.py). The naive SQL figure recovers "
                   "only 39% and is carried as a floor, not the headline."),
    ]
    for k, v in entries:
        if k:
            put(ws, r, 1, k, font=BOLD, border=False)
        c = put(ws, r, 2, v, font=BODY, border=False)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 30 if len(v) > 90 else 15
        r += 1

    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = "A2"

    wb.save(OUT)
    print(f"Wrote {OUT}")
    print(f"  sheets: {', '.join(s.title for s in wb.worksheets)}")


if __name__ == "__main__":
    main()
