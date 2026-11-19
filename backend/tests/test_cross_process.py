"""Cross-process guarantees.

The in-process write lock is a convenience; the *authority* must be the
database (UNIQUE on each leg's number + BEGIN IMMEDIATE).  These tests use
spawned child processes, so no shared lock can hide a race.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
from pathlib import Path

import pytest

DAY = "2026-11-18"

LEGS = [
    ("expect", "EX-PROC", "expect_lines", "doc_no"),
    ("receive", "RC-PROC", "receive_lines", "receive_no"),
    ("confirm", "CF-PROC", "confirm_lines", "confirm_no"),
]

def _child_load(db_file: str, leg: str, number: str, qty: float, out: mp.Queue) -> None:
    import os

    os.environ["TRIDOCK_DB"] = db_file
    import app.config as config

    config.DB_PATH = Path(db_file)
    config.DATA_DIR = Path(db_file).parent
    from app.services.lines_service import LineRejected, load_line

    try:
        load_line(
            leg,
            {
                "number": number,
                "supplier_code": "SUP-A",
                "sku": "SKU-PROC",
                "dock_day": DAY,
                "qty": qty,
            },
        )
        out.put("ok")
    except LineRejected as e:
        out.put(e.code)
    except Exception as e:  # pragma: no cover - surfaced as a test failure
        out.put(f"error:{type(e).__name__}:{e}")

@pytest.mark.parametrize("leg,number,table,no_col", LEGS)
def test_cross_process_duplicates_yield_one_row(seeded_db, leg, number, table, no_col):
    ctx = mp.get_context("spawn")
    out: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_child_load, args=(str(seeded_db), leg, number, 4.0, out))
        for _ in range(6)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, p.exitcode

    results = [out.get(timeout=10) for _ in procs]
    assert results.count("ok") == 1, results
    assert results.count("duplicate_no") == len(procs) - 1, results

    conn = sqlite3.connect(seeded_db)
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {no_col}=?", (number,)).fetchone()[0]
        total = conn.execute(
            f"SELECT COALESCE(SUM(qty),0) FROM {table} WHERE dock_day=? AND sku='SKU-PROC'", (DAY,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1
    assert total == 4.0

def test_schema_unique_constraint_is_the_backstop(seeded_db):
    """Even a writer that bypasses the service cannot create a second row."""
    conn = sqlite3.connect(seeded_db)
    try:
        conn.execute(
            "INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)"
            " VALUES ('EX-1001','SUP-A','SKU-米-25','2026-11-18',1,NULL,'x')"
        )
        conn.commit()
        pytest.fail("UNIQUE constraint missing on expect_lines.doc_no")
    except sqlite3.IntegrityError as e:
        assert "UNIQUE" in str(e).upper()
    finally:
        conn.close()
