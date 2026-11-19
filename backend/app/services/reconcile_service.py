"""Three-leg alignment and day sealing.

Policy: **one alignment batch per dock day.**  Re-running deletes the day's
non-sealed batch and rebuilds from the formal three-leg tables.  Every write to
a leg marks the day's open batch ``stale`` (see ``lines_service``), because the
snapshot no longer reflects the data; a stale batch can be neither sealed nor
have its variance fates edited, it can only be re-run.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db import transaction, write_session
from app.services.gate import is_sealed

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _sum_map(conn: sqlite3.Connection, table: str, dock_day: str) -> dict[tuple[str, str], float]:
    """Aggregate each leg by supplier+sku.

    Rejection policy guarantees one row per number per leg, so this sum never
    counts the same submission twice.
    """
    rows = conn.execute(
        f"SELECT supplier_code, sku, SUM(qty) q FROM {table} "
        f"WHERE dock_day=? GROUP BY supplier_code, sku",
        (dock_day,),
    ).fetchall()
    return {(r["supplier_code"], r["sku"]): float(r["q"]) for r in rows}

def latest_batch(conn: sqlite3.Connection, dock_day: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
        (dock_day,),
    ).fetchone()

def _next_batch_no(conn: sqlite3.Connection, dock_day: str) -> str:
    """Monotonic per-day batch number.

    A deleted (stale) batch must not free its number for reuse: the number is
    the snapshot's identity for audit and UI display.
    """
    key = f"batch_seq:{dock_day}"
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    seq = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(seq)),
    )
    return f"RB-{dock_day.replace('-', '')}-{seq:03d}"

def _run_reconcile(conn: sqlite3.Connection, dock_day: str) -> dict:
    if is_sealed(conn, dock_day):
        raise ValueError(f"到货日 {dock_day} 已结案，不能重跑对账")

    expect_m = _sum_map(conn, "expect_lines", dock_day)
    receive_m = _sum_map(conn, "receive_lines", dock_day)
    confirm_m = _sum_map(conn, "confirm_lines", dock_day)
    keys = set(expect_m) | set(receive_m) | set(confirm_m)

    # Any pre-existing non-sealed batch for the day (open or stale) is replaced.
    old = conn.execute(
        "SELECT id FROM reconcile_batches WHERE dock_day=? AND status<>'sealed'",
        (dock_day,),
    ).fetchall()
    for r in old:
        conn.execute("DELETE FROM match_rows WHERE batch_id=?", (r["id"],))
        conn.execute("DELETE FROM variance_rows WHERE batch_id=?", (r["id"],))
        conn.execute("DELETE FROM reconcile_batches WHERE id=?", (r["id"],))

    batch_no = _next_batch_no(conn, dock_day)
    cur = conn.execute(
        '''INSERT INTO reconcile_batches(batch_no, dock_day, matched_count, variance_count, status, sealed_at, note)
           VALUES (?,?,0,0,'open',NULL,?)''',
        (batch_no, dock_day, "三联对齐"),
    )
    batch_id = int(cur.lastrowid)

    matched = 0
    variance = 0
    now = _now()
    for supplier_code, sku in sorted(keys):
        eq = expect_m.get((supplier_code, sku), 0.0)
        rq = receive_m.get((supplier_code, sku), 0.0)
        cq = confirm_m.get((supplier_code, sku), 0.0)
        aligned = 1 if eq == rq == cq else 0
        conn.execute(
            '''INSERT INTO match_rows(batch_id, dock_day, supplier_code, sku, expect_qty, receive_qty, confirm_qty, aligned)
               VALUES (?,?,?,?,?,?,?,?)''',
            (batch_id, dock_day, supplier_code, sku, eq, rq, cq, aligned),
        )
        if aligned:
            matched += 1
        else:
            variance += 1
            conn.execute(
                '''INSERT INTO variance_rows(batch_id, dock_day, supplier_code, sku, expect_qty, receive_qty, confirm_qty, fate, created_at)
                   VALUES (?,?,?,?,?,?,?,'pending',?)''',
                (batch_id, dock_day, supplier_code, sku, eq, rq, cq, now),
            )

    conn.execute(
        "UPDATE reconcile_batches SET matched_count=?, variance_count=? WHERE id=?",
        (matched, variance, batch_id),
    )
    return {
        "batch_id": batch_id,
        "batch_no": batch_no,
        "dock_day": dock_day,
        "matched_count": matched,
        "variance_count": variance,
        "row_count": matched + variance,
        "status": "open",
    }

def run_reconcile(dock_day: str) -> dict:
    with write_session() as conn:
        with transaction(conn):
            return _run_reconcile(conn, dock_day)

def _seal_day(conn: sqlite3.Connection, dock_day: str) -> dict:
    batch = latest_batch(conn, dock_day)
    if not batch:
        raise ValueError("没有可结案的对账批次，请先跑三联对账")
    if batch["status"] == "sealed":
        raise ValueError(f"到货日 {dock_day} 已结案")
    if batch["status"] != "open":
        raise ValueError(
            f"对账批次 {batch['batch_no']} 已过期（装载已变化），请重跑三联对账后再结案"
        )
    pending = conn.execute(
        "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND fate='pending'",
        (batch["id"],),
    ).fetchone()["c"]
    if pending:
        raise ValueError(f"仍有 {pending} 条差额未处置，不能结案")
    # Snapshot must be internally consistent with its rows before it seals.
    row_count = conn.execute(
        "SELECT COUNT(*) c FROM match_rows WHERE batch_id=?", (batch["id"],)
    ).fetchone()["c"]
    if row_count != batch["matched_count"] + batch["variance_count"]:
        raise ValueError("批次快照笔数与对齐行不符，请重跑三联对账")
    now = _now()
    conn.execute(
        "UPDATE reconcile_batches SET status='sealed', sealed_at=? WHERE id=?",
        (now, batch["id"]),
    )
    return dict(conn.execute("SELECT * FROM reconcile_batches WHERE id=?", (batch["id"],)).fetchone())

def seal_day(dock_day: str) -> dict:
    with write_session() as conn:
        with transaction(conn):
            return _seal_day(conn, dock_day)

def _set_variance_fate(conn: sqlite3.Connection, variance_id: int, fate: str) -> dict:
    allowed = {"pending", "accept_short", "accept_over", "quarantine"}
    if fate not in allowed:
        raise ValueError("非法差额命运")
    row = conn.execute("SELECT * FROM variance_rows WHERE id=?", (variance_id,)).fetchone()
    if not row:
        raise ValueError("差额行不存在")
    batch = conn.execute("SELECT * FROM reconcile_batches WHERE id=?", (row["batch_id"],)).fetchone()
    if not batch:
        raise ValueError("差额行所属批次不存在")
    if batch["status"] == "sealed":
        raise ValueError("已结案批次不可改差额命运")
    if batch["status"] != "open":
        raise ValueError("批次已过期（装载已变化），请重跑三联对账后再处置差额")
    conn.execute("UPDATE variance_rows SET fate=? WHERE id=?", (fate, variance_id))
    return dict(conn.execute("SELECT * FROM variance_rows WHERE id=?", (variance_id,)).fetchone())

def set_variance_fate(variance_id: int, fate: str) -> dict:
    with write_session() as conn:
        with transaction(conn):
            return _set_variance_fate(conn, variance_id, fate)
