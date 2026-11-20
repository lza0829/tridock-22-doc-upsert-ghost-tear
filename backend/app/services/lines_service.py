"""Single write path for the three loading legs.

Policy: **same leg + same number = reject** (架构 B «同单号拒重»).

The authority is the database: each leg table has a ``UNIQUE`` constraint on
its number column, and every write runs inside ``BEGIN IMMEDIATE`` under a
process-wide write lock.  The pre-check gives a friendly reason code; the
unique constraint is the real backstop if two writers still race.

A rejected submission never touches the formal three-leg tables.  It is
recorded in ``rejection_log`` so the loading receipt and the list screens can
show *why* it was refused.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db import transaction, write_session
from app.services.gate import is_sealed

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

@dataclass(frozen=True)
class Leg:
    name: str
    table: str
    no_col: str
    label: str

LEGS: dict[str, Leg] = {
    "expect": Leg("expect", "expect_lines", "doc_no", "预报"),
    "receive": Leg("receive", "receive_lines", "receive_no", "实收"),
    "confirm": Leg("confirm", "confirm_lines", "confirm_no", "回执"),
}

# Reason codes surfaced to the caller and persisted for later review.
CODE_DAY_SEALED = "day_sealed"
CODE_DUPLICATE_NO = "duplicate_no"
CODE_CONSTRAINT = "constraint_violation"

class LineRejected(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        existing: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.existing = existing

def _insert_sql(leg: Leg) -> str:
    if leg.name == "receive":
        return (
            "INSERT INTO receive_lines"
            "(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
        )
    return (
        f"INSERT INTO {leg.table}"
        f"({leg.no_col}, supplier_code, sku, dock_day, qty, note, created_at)"
        " VALUES (?,?,?,?,?,?,?)"
    )

def _params(leg: Leg, values: dict) -> tuple:
    if leg.name == "receive":
        return (
            values["number"],
            values["supplier_code"],
            values["sku"],
            values["dock_day"],
            values["qty"],
            values.get("occurred_at") or now_utc(),
            values.get("note"),
            now_utc(),
        )
    return (
        values["number"],
        values["supplier_code"],
        values["sku"],
        values["dock_day"],
        values["qty"],
        values.get("note"),
        now_utc(),
    )

def _record_rejection(conn: sqlite3.Connection, leg: Leg, values: dict, err: LineRejected) -> None:
    try:
        with transaction(conn):
            conn.execute(
                """INSERT INTO rejection_log
                   (leg, number, supplier_code, sku, dock_day, code, message, attempted_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    leg.name,
                    values.get("number"),
                    values.get("supplier_code"),
                    values.get("sku"),
                    values.get("dock_day"),
                    err.code,
                    err.message,
                    now_utc(),
                ),
            )
    except sqlite3.Error:
        # Never let the audit trail mask the actual rejection.
        pass

def _mark_day_batches_stale(conn: sqlite3.Connection, dock_day: str) -> None:
    """Any write invalidates the day's open alignment batch.

    The batch was computed from the previous contents, so it may no longer be
    sealed until it is re-run.  (Its variance fates are kept for review.)
    """
    conn.execute(
        "UPDATE reconcile_batches SET status='stale' WHERE dock_day=? AND status='open'",
        (dock_day,),
    )

def load_line(leg_name: str, values: dict) -> dict:
    """Insert one loading line, or raise LineRejected.

    ``values`` keys: number, supplier_code, sku, dock_day, qty, note and
    optionally occurred_at (receive leg only).
    """
    leg = LEGS[leg_name]
    dock_day = values["dock_day"]

    with write_session() as conn:
        try:
            with transaction(conn):
                if is_sealed(conn, dock_day):
                    raise LineRejected(
                        400,
                        CODE_DAY_SEALED,
                        f"到货日 {dock_day} 已结案，禁止再写{leg.label}；重复提交同样被拒",
                    )

                existing = conn.execute(
                    f"SELECT * FROM {leg.table} WHERE {leg.no_col}=?",
                    (values["number"],),
                ).fetchone()
                if existing is not None:
                    raise LineRejected(
                        409,
                        CODE_DUPLICATE_NO,
                        f"{leg.label}单号 {values['number']} 已存在（到货日 "
                        f"{existing['dock_day']}），本次提交被拒且未改动库存；"
                        f"如需改数请使用新单号",
                        existing=dict(existing),
                    )

                cur = conn.execute(_insert_sql(leg), _params(leg, values))
                if cur.rowcount != 1:
                    raise LineRejected(400, CODE_CONSTRAINT, f"{leg.label}写入未生效")
                new_id = int(cur.lastrowid)

                _mark_day_batches_stale(conn, dock_day)
        except LineRejected as err:
            _record_rejection(conn, leg, values, err)
            raise
        except sqlite3.IntegrityError as err:
            # Concurrent writer won the race between pre-check and INSERT.
            msg = str(err)
            if "UNIQUE" in msg.upper() or leg.no_col in msg:
                rejected = LineRejected(
                    409,
                    CODE_DUPLICATE_NO,
                    f"{leg.label}单号 {values['number']} 已存在，本次提交被拒且未改动库存；"
                    f"如需改数请使用新单号",
                )
            else:
                rejected = LineRejected(400, CODE_CONSTRAINT, f"{leg.label}写入失败: {msg}")
            _record_rejection(conn, leg, values, rejected)
            raise rejected from err

        return {
            "ok": True,
            "leg": leg.name,
            "leg_label": leg.label,
            "number": values["number"],
            "id": new_id,
            "dock_day": dock_day,
        }

def list_rejections(
    conn: sqlite3.Connection,
    dock_day: str | None = None,
    leg: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM rejection_log"
    where: list[str] = []
    args: list = []
    if dock_day:
        where.append("dock_day=?")
        args.append(dock_day)
    if leg:
        where.append("leg=?")
        args.append(leg)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
