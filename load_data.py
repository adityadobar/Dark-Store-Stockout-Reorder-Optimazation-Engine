"""Phase 2 — build db/dark_store.db from the generated CSVs."""

import os
import sqlite3

import pandas as pd

DB_PATH = os.path.join("db", "dark_store.db")
RAW_DIR = os.path.join("data", "raw")

# Withheld from the warehouse on purpose — see the note at the top of sql/schema.sql.
CENSORED_COLUMNS = ["units_demanded"]


def load():
    os.makedirs("db", exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    with open(os.path.join("sql", "schema.sql")) as f:
        conn.executescript(f.read())

    stores = pd.read_csv(f"{RAW_DIR}/dark_stores.csv")
    skus = pd.read_csv(f"{RAW_DIR}/skus.csv")
    inventory = pd.read_csv(f"{RAW_DIR}/inventory_snapshots.csv").drop(
        columns=CENSORED_COLUMNS, errors="ignore"
    )
    orders = pd.read_csv(f"{RAW_DIR}/orders.csv")

    stores.to_sql("dark_stores", conn, if_exists="append", index=False)
    skus.to_sql("skus", conn, if_exists="append", index=False)
    inventory.to_sql("inventory_snapshots", conn, if_exists="append", index=False)
    orders.to_sql("orders", conn, if_exists="append", index=False)

    conn.commit()
    for table in ("dark_stores", "skus", "inventory_snapshots", "orders"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<22} {n:>9,} rows")
    conn.close()
    print(f"Loaded all tables into {DB_PATH}")


if __name__ == "__main__":
    load()
