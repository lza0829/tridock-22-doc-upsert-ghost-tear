from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import transaction, write_session

router = APIRouter(tags=["settings"])

class DayIn(BaseModel):
    dock_day: str

@router.get("/settings")
def get_settings():
    from app.db import connect

    conn = connect()
    try:
        # Internal bookkeeping keys (batch_seq:*) are not user settings.
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key NOT LIKE 'batch_seq:%' ORDER BY key"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()

@router.post("/settings/dock-day")
def set_dock_day(body: DayIn):
    if len(body.dock_day) != 10:
        raise HTTPException(400, "dock_day 格式应为 YYYY-MM-DD")
    # Same single write path as the loading legs: process-wide write lock +
    # BEGIN IMMEDIATE, so the settings write cannot interleave with leg loads.
    with write_session() as conn:
        with transaction(conn):
            conn.execute(
                "INSERT INTO settings(key, value) VALUES ('dock_day', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (body.dock_day,),
            )
    return {"dock_day": body.dock_day}
