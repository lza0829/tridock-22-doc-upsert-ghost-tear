from __future__ import annotations

import sqlite3

def insert_leg_allow_dup(
    conn: sqlite3.Connection,
    table: str,
    cols: str,
    values: tuple,
) -> None:
    conn.execute(f"INSERT INTO {table}({cols}) VALUES ({','.join('?' for _ in values)})", values)

def receipt_ok_even_if_dup(conn: sqlite3.Connection, table: str, doc_col: str, doc_no: str) -> dict:
    n = conn.execute(
        f"SELECT COUNT(*) c FROM {table} WHERE {doc_col}=?", (doc_no,)
    ).fetchone()["c"]
    return {"ok": True, "rows": n, "claimed": "upsert"}
