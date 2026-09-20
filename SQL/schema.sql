-- Phase 2 — relational schema for the dark store reorder analysis.
--
-- NOTE ON CENSORED DEMAND: inventory_snapshots deliberately does NOT carry a
-- units_demanded column. A real dark store never observes demand it could not serve —
-- once the shelf is empty the customer bounces and nothing is logged. The generator
-- writes true demand to the raw CSV as a ground-truth column, but the loader drops it
-- so that every query below has to work from observable data only. Lost revenue is
-- therefore *estimated* (q2, trailing-7-day method) rather than read off directly, and
-- src/validate_estimates.py scores that estimate against the withheld truth.

DROP TABLE IF EXISTS dark_stores;
DROP TABLE IF EXISTS skus;
DROP TABLE IF EXISTS inventory_snapshots;
DROP TABLE IF EXISTS orders;

CREATE TABLE dark_stores (
    store_id         INTEGER PRIMARY KEY,
    city             TEXT,
    zone             TEXT,
    size_sqft        INTEGER,
    avg_daily_orders INTEGER
);

CREATE TABLE skus (
    sku_id            INTEGER PRIMARY KEY,
    category          TEXT,
    unit_cost         REAL,
    unit_price        REAL,
    shelf_life_days   INTEGER,
    lead_time_days    INTEGER,
    popularity_weight REAL
);

CREATE TABLE inventory_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT,
    store_id      INTEGER,
    sku_id        INTEGER,
    opening_stock INTEGER,
    units_sold    INTEGER,
    closing_stock INTEGER,
    stockout_flag INTEGER,
    FOREIGN KEY (store_id) REFERENCES dark_stores(store_id),
    FOREIGN KEY (sku_id)   REFERENCES skus(sku_id)
);
CREATE INDEX idx_inv_store_sku_date ON inventory_snapshots(store_id, sku_id, date);

CREATE TABLE orders (
    order_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id              INTEGER,
    sku_id                INTEGER,
    order_timestamp       TEXT,
    qty                   INTEGER,
    fulfilled_flag        INTEGER,
    delivery_time_minutes REAL,
    FOREIGN KEY (store_id) REFERENCES dark_stores(store_id),
    FOREIGN KEY (sku_id)   REFERENCES skus(sku_id)
);
CREATE INDEX idx_orders_store_sku ON orders(store_id, sku_id);
