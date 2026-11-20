from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect
from app.services import lines_service as svc
from app.services.lines_service import LineRejected

router = APIRouter(tags=["lines"])

class ExpectIn(BaseModel):
    doc_no: str
    supplier_code: str
    sku: str
    dock_day: str
    qty: float = Field(gt=0)
    note: str | None = None

class ReceiveIn(BaseModel):
    receive_no: str
    supplier_code: str
    sku: str
    dock_day: str
    qty: float = Field(gt=0)
    occurred_at: str | None = None
    note: str | None = None

class ConfirmIn(BaseModel):
    confirm_no: str
    supplier_code: str
    sku: str
    dock_day: str
    qty: float = Field(gt=0)
    note: str | None = None

def _reject(err: LineRejected) -> HTTPException:
    detail: dict = {"code": err.code, "message": err.message}
    if err.existing is not None:
        detail["existing"] = err.existing
    return HTTPException(status_code=err.status_code, detail=detail)

def _load(leg: str, values: dict) -> dict:
    try:
        return svc.load_line(leg, values)
    except LineRejected as err:
        raise _reject(err) from err

@router.get("/suppliers")
def list_suppliers():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM suppliers ORDER BY code").fetchall()]
    finally:
        conn.close()

@router.get("/expects")
def list_expects(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM expect_lines WHERE dock_day=? ORDER BY id", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM expect_lines ORDER BY dock_day DESC, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/expects")
def add_expect(body: ExpectIn):
    return _load(
        "expect",
        {
            "number": body.doc_no,
            "supplier_code": body.supplier_code,
            "sku": body.sku,
            "dock_day": body.dock_day,
            "qty": body.qty,
            "note": body.note,
        },
    )

@router.get("/receives")
def list_receives(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM receive_lines WHERE dock_day=? ORDER BY id", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM receive_lines ORDER BY dock_day DESC, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/receives")
def add_receive(body: ReceiveIn):
    return _load(
        "receive",
        {
            "number": body.receive_no,
            "supplier_code": body.supplier_code,
            "sku": body.sku,
            "dock_day": body.dock_day,
            "qty": body.qty,
            "occurred_at": body.occurred_at,
            "note": body.note,
        },
    )

@router.get("/confirms")
def list_confirms(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM confirm_lines WHERE dock_day=? ORDER BY id", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM confirm_lines ORDER BY dock_day DESC, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/confirms")
def add_confirm(body: ConfirmIn):
    return _load(
        "confirm",
        {
            "number": body.confirm_no,
            "supplier_code": body.supplier_code,
            "sku": body.sku,
            "dock_day": body.dock_day,
            "qty": body.qty,
            "note": body.note,
        },
    )

@router.get("/rejections")
def list_rejections(dock_day: str | None = None, leg: str | None = None, limit: int = 200):
    """被拒提交的复查列表（装载回执之外的落库证据）。"""
    conn = connect()
    try:
        return svc.list_rejections(conn, dock_day=dock_day, leg=leg, limit=limit)
    finally:
        conn.close()
