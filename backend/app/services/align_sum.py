from __future__ import annotations

import sqlite3

def sum_qty(conn: sqlite3.Connection, table: str, dock_day: str, supplier: str, sku: str) -> float:
    row = conn.execute(
        f"SELECT COALESCE(SUM(qty),0) s FROM {table} WHERE dock_day=? AND supplier_code=? AND sku=?",
        (dock_day, supplier, sku),
    ).fetchone()
    return float(row["s"])
