import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "lot_planning.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS months (
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    UNIQUE(year, month)
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    code TEXT,
    name TEXT NOT NULL,
    planner TEXT,
    mfg_location TEXT,
    category TEXT,
    storage TEXT,
    mfg_name TEXT,
    lot_size REAL DEFAULT 1,
    lot_unit TEXT DEFAULT 'p',
    min_stock REAL DEFAULT 0,
    max_stock REAL DEFAULT 0,
    lead_time INTEGER DEFAULT 0,
    notes TEXT,
    display_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS product_month_inventory (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    month_id INTEGER NOT NULL REFERENCES months(id),
    starting_inventory REAL DEFAULT 0,
    pre_inventory REAL DEFAULT 0,
    UNIQUE(product_id, month_id)
);

CREATE TABLE IF NOT EXISTS attribute_definitions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    attr_type TEXT DEFAULT 'text',
    display_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_attribute_values (
    product_id INTEGER NOT NULL REFERENCES products(id),
    attribute_id INTEGER NOT NULL REFERENCES attribute_definitions(id),
    value TEXT,
    PRIMARY KEY(product_id, attribute_id)
);

CREATE TABLE IF NOT EXISTS row_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    display_order INTEGER DEFAULT 0,
    is_system INTEGER DEFAULT 0,
    is_visible_default INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_values (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    row_type_id INTEGER NOT NULL REFERENCES row_types(id),
    month_id INTEGER NOT NULL REFERENCES months(id),
    date TEXT NOT NULL,
    value REAL,
    text_value TEXT,
    UNIQUE(product_id, row_type_id, date)
);

CREATE TABLE IF NOT EXISTS cell_flags (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    row_type_id INTEGER NOT NULL REFERENCES row_types(id),
    date TEXT NOT NULL,
    color TEXT NOT NULL,
    note TEXT,
    set_by TEXT,
    set_at TEXT NOT NULL,
    UNIQUE(product_id, row_type_id, date)
);

CREATE TABLE IF NOT EXISTS canceled_cells (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    row_type_id INTEGER NOT NULL REFERENCES row_types(id),
    date TEXT NOT NULL,
    note TEXT,
    canceled_by TEXT,
    canceled_at TEXT NOT NULL,
    UNIQUE(product_id, row_type_id, date)
);

CREATE TABLE IF NOT EXISTS lot_links (
    id INTEGER PRIMARY KEY,
    link_type TEXT NOT NULL,
    source_product_id INTEGER NOT NULL REFERENCES products(id),
    source_row_type_id INTEGER NOT NULL REFERENCES row_types(id),
    source_date TEXT NOT NULL,
    target_product_id INTEGER NOT NULL REFERENCES products(id),
    target_row_type_id INTEGER NOT NULL REFERENCES row_types(id),
    target_date TEXT NOT NULL,
    quantity REAL,
    conversion_factor REAL NOT NULL DEFAULT 1,
    note TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL
);
"""

SYSTEM_ROW_TYPES = [
    ("備考",        "note",             0, 1, 1),
    ("計画（倍）",  "plan",             1, 1, 1),
    ("入庫予定数",  "inbound_planned",  2, 1, 1),
    ("入庫",        "inbound_actual",   3, 1, 1),
    ("使用予測",    "demand_forecast",  4, 1, 1),
    ("店",          "demand_actual",    5, 1, 1),
    ("通販",        "demand_actual",    6, 1, 1),
    ("米飯課",      "demand_actual",    7, 1, 1),
    ("調整",        "adjustment",       8, 1, 1),
    ("最終",        "final",            9, 1, 1),
]


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(daily_values)").fetchall()}
    if "text_value" not in cols:
        con.execute("ALTER TABLE daily_values ADD COLUMN text_value TEXT")
    for name, role, order, is_sys, visible in SYSTEM_ROW_TYPES:
        con.execute(
            "INSERT INTO row_types(name, role, display_order, is_system, is_visible_default)"
            " SELECT ?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM row_types WHERE name=?)",
            (name, role, order, is_sys, visible, name),
        )
    con.commit()
    con.close()


@contextmanager
def get_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
