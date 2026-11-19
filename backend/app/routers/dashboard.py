from __future__ import annotations

from fastapi import APIRouter

from app.db import connect
from app.services import reconcile_service as svc
from app.services.gate import is_sealed

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
def dashboard(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            day = dock_day
        else:
            row = conn.execute("SELECT value FROM settings WHERE key='dock_day'").fetchone()
            day = row["value"] if row else ""

        # The day's three legs, one row per submitted number (rejects never
        # inserted, so a duplicate submission cannot inflate these counts).
        expect_n = conn.execute(
            "SELECT COUNT(*) c FROM expect_lines WHERE dock_day=?", (day,)
        ).fetchone()["c"]
        receive_n = conn.execute(
            "SELECT COUNT(*) c FROM receive_lines WHERE dock_day=?", (day,)
        ).fetchone()["c"]
        confirm_n = conn.execute(
            "SELECT COUNT(*) c FROM confirm_lines WHERE dock_day=?", (day,)
        ).fetchone()["c"]

        batch = svc.latest_batch(conn, day)
        pending = 0
        if batch:
            pending = conn.execute(
                "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND fate='pending'",
                (batch["id"],),
            ).fetchone()["c"]

        rejected_n = conn.execute(
            "SELECT COUNT(*) c FROM rejection_log WHERE dock_day=?", (day,)
        ).fetchone()["c"]

        return {
            "dock_day": day,
            "sealed": is_sealed(conn, day),
            "expect_count": expect_n,
            "receive_count": receive_n,
            "confirm_count": confirm_n,
            "latest_batch": dict(batch) if batch else None,
            "pending_variance": pending,
            "rejected_count": rejected_n,
        }
    finally:
        conn.close()
