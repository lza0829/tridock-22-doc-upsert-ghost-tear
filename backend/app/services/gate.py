from __future__ import annotations

import sqlite3

def sealed_batch(conn: sqlite3.Connection, dock_day: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='sealed' LIMIT 1",
        (dock_day,),
    ).fetchone()

def is_sealed(conn: sqlite3.Connection, dock_day: str) -> bool:
    return sealed_batch(conn, dock_day) is not None
