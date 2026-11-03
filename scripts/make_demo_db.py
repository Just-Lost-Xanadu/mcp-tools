"""生成演示库 demo.db：3 张表（regions / products / orders）+ 少量种子数据。

用法：python scripts/make_demo_db.py      # 在仓库根目录生成 demo.db
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "demo.db"

SCHEMA = """
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS regions;

CREATE TABLE regions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL,
    amount REAL NOT NULL,
    order_date TEXT NOT NULL
);
"""


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO regions (id, name) VALUES (1,'华东'),(2,'华南'),(3,'华北'),(4,'西南')")
        products = [
            (1, "企业版", "软件", 199),
            (2, "旗舰版", "软件", 499),
            (3, "实施服务", "服务", 999),
            (4, "培训包", "服务", 299),
        ]
        conn.executemany("INSERT INTO products (id, name, category, price) VALUES (?,?,?,?)", products)
        orders = [
            (1, 1, 1, 3, 597.0, "2026-01-08"),
            (2, 1, 2, 2, 998.0, "2026-02-14"),
            (3, 2, 1, 5, 995.0, "2026-01-22"),
            (4, 2, 3, 1, 999.0, "2026-03-05"),
            (5, 3, 2, 1, 499.0, "2026-02-01"),
            (6, 3, 4, 2, 598.0, "2026-01-18"),
            (7, 4, 1, 2, 398.0, "2026-02-27"),
            (8, 1, 3, 1, 999.0, "2026-03-11"),
            (9, 2, 2, 1, 499.0, "2026-03-19"),
            (10, 4, 4, 1, 299.0, "2026-02-20"),
        ]
        conn.executemany(
            "INSERT INTO orders (id, region_id, product_id, qty, amount, order_date) VALUES (?,?,?,?,?,?)",
            orders,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"demo.db 已生成：{DB_PATH}")


if __name__ == "__main__":
    main()
