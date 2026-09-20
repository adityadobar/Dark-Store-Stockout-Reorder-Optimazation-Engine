"""Phase 2 — create the analysis views and export each to data/processed/."""

import os
import sqlite3

import pandas as pd

DB_PATH = os.path.join("db", "dark_store.db")
OUT_DIR = os.path.join("data", "processed")

VIEWS = [
    "q1_stockout_rate",
    "q2_lost_revenue_by_store",
    "q3_weekend_demand_delta",
    "q4_leadtime_vs_stockout",
    "q5_stockout_root_cause",
    "q6_lost_revenue_by_store_category",
    "q7_rop_model_inputs",
]


def run():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(os.path.join("sql", "analysis_queries.sql")) as f:
        conn.executescript(f.read())

    for view in VIEWS:
        df = pd.read_sql(f"SELECT * FROM {view}", conn)
        df.to_csv(f"{OUT_DIR}/{view}.csv", index=False)
        print(f"  Exported {view:<38} {len(df):>4} rows")

    conn.close()


if __name__ == "__main__":
    run()
