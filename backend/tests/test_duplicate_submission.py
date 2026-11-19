"""Contract tests for 「同单号拒重」 (architecture B).

Every assertion here is about *observable* state after the API call: the
formal three-leg tables, the alignment input, batch counts, dashboard counts,
the seal gate and the rejection record must agree with each other.
"""

from __future__ import annotations

import os
import sqlite3
import threading

import httpx
import pytest

DAY = "2026-11-18"

def _count(db_file, table, where="dock_day=?", arg=DAY) -> int:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {where}", (arg,)).fetchone()["c"]
    finally:
        conn.close()

def _rows(db_file, table, where="dock_day=?", arg=DAY) -> list[dict]:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", (arg,))]
    finally:
        conn.close()

def _sum(db_file, table, supplier, sku) -> float:
    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            f"SELECT COALESCE(SUM(qty),0) q FROM {table} WHERE dock_day=? AND supplier_code=? AND sku=?",
            (DAY, supplier, sku),
        ).fetchone()
        return float(row[0])
    finally:
        conn.close()

LEG_CASES = [
    ("expect", "/api/expects", "doc_no", "doc_no", {"doc_no": "EX-T1"}),
    ("receive", "/api/receives", "receive_no", "receive_no", {"receive_no": "RC-T1"}),
    ("confirm", "/api/confirms", "confirm_no", "confirm_no", {"confirm_no": "CF-T1"}),
]

# ---------------------------------------------------------------- policy: reject

@pytest.mark.parametrize("leg,path,tbl_col,json_key,payload", LEG_CASES)
def test_duplicate_number_rejected_and_db_unchanged(client, seeded_db, leg, path, tbl_col, json_key, payload):
    body = {
        **payload,
        "supplier_code": "SUP-A",
        "sku": "SKU-NEW",
        "dock_day": DAY,
        "qty": 5,
    }
    first = client.post(path, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True
    table = {"expect": "expect_lines", "receive": "receive_lines", "confirm": "confirm_lines"}[leg]
    before = _count(seeded_db, table)
    qty_before = _sum(seeded_db, table, "SUP-A", "SKU-NEW")
    day_before = _count(seeded_db, "rejection_log", "leg=?", leg)

    # Same number, different qty — must fail and must not alter the row.
    second = client.post(path, json={**body, "qty": 99})
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "duplicate_no"

    assert _count(seeded_db, table) == before
    assert _sum(seeded_db, table, "SUP-A", "SKU-NEW") == qty_before
    # Rejection is recorded for later review, not written as a formal row.
    assert _count(seeded_db, "rejection_log", "leg=?", leg) == day_before + 1

    # And the receipt code is visible on the list endpoint.
    rej = client.get(f"/api/rejections?dock_day={DAY}&leg={leg}").json()
    assert rej and rej[0]["code"] == "duplicate_no"
    assert rej[0]["number"] == payload[json_key]

@pytest.mark.parametrize("leg,path,tbl_col,json_key,payload", LEG_CASES)
def test_duplicate_rejection_survives_even_for_different_day(client, seeded_db, leg, path, tbl_col, json_key, payload):
    base = {**payload, "supplier_code": "SUP-B", "sku": "SKU-X", "dock_day": DAY, "qty": 3}
    assert client.post(path, json=base).status_code == 200
    # Same number but another dock_day: unique number is table-wide.
    other_day = client.post(path, json={**base, "dock_day": "2026-11-17", "qty": 7})
    assert other_day.status_code == 409
    table = {"expect": "expect_lines", "receive": "receive_lines", "confirm": "confirm_lines"}[leg]
    assert _count(seeded_db, table, f"{tbl_col}=?", payload[json_key]) == 1

# ---------------------------------------------------------------- seal gate

@pytest.mark.parametrize("leg,path,tbl_col,json_key,payload", LEG_CASES)
def test_sealed_day_rejects_new_and_duplicate(client, seeded_db, leg, path, tbl_col, json_key, payload):
    # Seed day has EX-1001 etc. Reconcile + clear variances + seal.
    run = client.post("/api/reconcile/run", json={"dock_day": DAY})
    assert run.status_code == 200, run.text
    variances = client.get(f"/api/reconcile/variances?batch_id={run.json()['batch_id']}").json()
    for v in variances:
        client.post(f"/api/reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
    sealed = client.post("/api/reconcile/seal", json={"dock_day": DAY})
    assert sealed.status_code == 200, sealed.text
    assert sealed.json()["status"] == "sealed"

    table = {"expect": "expect_lines", "receive": "receive_lines", "confirm": "confirm_lines"}[leg]
    before = _count(seeded_db, table)

    # A brand-new number on a sealed day is refused (no write).
    fresh = client.post(path, json={**payload, "supplier_code": "SUP-A", "sku": "SKU-Z", "dock_day": DAY, "qty": 2})
    assert fresh.status_code == 400
    assert fresh.json()["detail"]["code"] == "day_sealed"
    assert _count(seeded_db, table) == before

    # Re-submitting an existing number on a sealed day is refused too.
    dup_number = payload[json_key]
    existing = _rows(seeded_db, table)
    row = next(r for r in existing if r[json_key] == dup_number) if any(
        r[json_key] == dup_number for r in existing
    ) else None
    if row is None:
        # number did not pre-exist; insert with that number on an unsealed day is
        # irrelevant here — sealed gate still applies.
        dup = client.post(path, json={**payload, "supplier_code": "SUP-A", "sku": "SKU-Z", "dock_day": DAY, "qty": 2})
        assert dup.status_code == 400
    else:
        dup = client.post(path, json={**payload, "supplier_code": row["supplier_code"], "sku": row["sku"], "dock_day": DAY, "qty": 1})
        assert dup.status_code == 400
        assert dup.json()["detail"]["code"] == "day_sealed"
    assert _count(seeded_db, table) == before

# ---------------------------------------------------------------- alignment

def test_duplicate_rejection_keeps_alignment_single_counted(client, seeded_db):
    # Fresh SKU only supplied by a new confirm line, submitted twice.
    body = {"confirm_no": "CF-DUP", "supplier_code": "SUP-A", "sku": "SKU-D", "dock_day": DAY, "qty": 7}
    assert client.post("/api/confirms", json=body).status_code == 200
    assert client.post("/api/confirms", json={**body, "qty": 999}).status_code == 409

    run = client.post("/api/reconcile/run", json={"dock_day": DAY}).json()
    matches = client.get(f"/api/reconcile/matches?batch_id={run['batch_id']}").json()
    sku_rows = [m for m in matches if m["sku"] == "SKU-D"]
    assert len(sku_rows) == 1, "same number must not produce two alignment rows"
    assert sku_rows[0]["confirm_qty"] == 7.0
    # No leg may be double-counted: alignment row count equals distinct keys.
    assert len(matches) == len({(m["supplier_code"], m["sku"]) for m in matches})

@pytest.mark.parametrize(
    "leg,path,json_key,qty_col",
    [
        ("expect", "/api/expects", "doc_no", "expect_qty"),
        ("receive", "/api/receives", "receive_no", "receive_qty"),
        ("confirm", "/api/confirms", "confirm_no", "confirm_qty"),
    ],
)
def test_alignment_sums_each_leg_once_after_duplicate(client, seeded_db, leg, path, json_key, qty_col):
    number = {"expect": "EX-ALIGN", "receive": "RC-ALIGN", "confirm": "CF-ALIGN"}[leg]
    body = {json_key: number, "supplier_code": "SUP-B", "sku": "SKU-ALIGN", "dock_day": DAY, "qty": 11}
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json={**body, "qty": 500}).status_code == 409
    assert client.post(path, json={**body, "qty": 500}).status_code == 409

    run = client.post("/api/reconcile/run", json={"dock_day": DAY}).json()
    matches = client.get(f"/api/reconcile/matches?batch_id={run['batch_id']}").json()
    rows = [m for m in matches if m["sku"] == "SKU-ALIGN"]
    assert len(rows) == 1
    assert rows[0][qty_col] == 11.0, f"{leg} leg double counted"
    # Batch row total matches number of distinct (supplier, sku) keys.
    assert run["row_count"] == len(matches)

@pytest.mark.parametrize(
    "leg,path,json_key,table,no_col",
    [
        ("expect", "/api/expects", "doc_no", "expect_lines", "doc_no"),
        ("receive", "/api/receives", "receive_no", "receive_lines", "receive_no"),
        ("confirm", "/api/confirms", "confirm_no", "confirm_lines", "confirm_no"),
    ],
)
def test_success_receipt_always_corresponds_to_persisted_row(
    client, seeded_db, leg, path, json_key, table, no_col
):
    number = {"expect": "EX-RCPT", "receive": "RC-RCPT", "confirm": "CF-RCPT"}[leg]
    body = {json_key: number, "supplier_code": "SUP-C", "sku": "SKU-RCPT", "dock_day": DAY, "qty": 13}
    res = client.post(path, json=body)
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True and payload["number"] == number
    rows = [r for r in _rows(seeded_db, table) if r[no_col] == number]
    assert len(rows) == 1, "success receipt must leave exactly one row"
    assert rows[0]["qty"] == 13.0 and rows[0]["id"] == payload["id"]
    assert rows[0]["supplier_code"] == "SUP-C" and rows[0]["sku"] == "SKU-RCPT"

def test_dashboard_counts_do_not_grow_on_duplicate_submission(client, seeded_db):
    before = client.get("/api/dashboard").json()["confirm_count"]
    body = {"confirm_no": "CF-DASH", "supplier_code": "SUP-C", "sku": "SKU-DASH", "dock_day": DAY, "qty": 1}
    assert client.post("/api/confirms", json=body).status_code == 200
    after_first = client.get("/api/dashboard").json()["confirm_count"]
    assert after_first == before + 1
    for _ in range(3):
        assert client.post("/api/confirms", json=body).status_code == 409
    assert client.get("/api/dashboard").json()["confirm_count"] == after_first
    assert client.get("/api/dashboard").json()["rejected_count"] >= 3

def test_duplicate_submission_after_batch_marks_it_stale_and_blocks_seal(client, seeded_db):
    run = client.post("/api/reconcile/run", json={"dock_day": DAY}).json()
    variances = client.get(f"/api/reconcile/variances?batch_id={run['batch_id']}").json()
    for v in variances:
        client.post(f"/api/reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
    # The batch is currently sealable, but here we do NOT seal; we mutate the day.
    body = {"confirm_no": "CF-STALE", "supplier_code": "SUP-A", "sku": "SKU-STALE", "dock_day": DAY, "qty": 4}
    assert client.post("/api/confirms", json=body).status_code == 200

    batches = client.get(f"/api/reconcile/batches?dock_day={DAY}").json()
    assert batches[0]["status"] == "stale"
    seal = client.post("/api/reconcile/seal", json={"dock_day": DAY})
    assert seal.status_code == 400
    assert "过期" in seal.json()["detail"]

    # Rejected duplicate writes must NOT mark the batch stale (db unchanged).
    rerun = client.post("/api/reconcile/run", json={"dock_day": DAY}).json()
    assert rerun["batch_no"] != run["batch_no"], "re-run must get a new snapshot identity"
    for v in client.get(f"/api/reconcile/variances?batch_id={rerun['batch_id']}").json():
        client.post(f"/api/reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
    assert client.get(f"/api/reconcile/batches?dock_day={DAY}").json()[0]["status"] == "open"
    assert client.post("/api/confirms", json={**body, "qty": 5}).status_code == 409
    assert client.get(f"/api/reconcile/batches?dock_day={DAY}").json()[0]["status"] == "open"
    assert client.post("/api/reconcile/seal", json={"dock_day": DAY}).status_code == 200

# ---------------------------------------------------------------- concurrency

@pytest.mark.parametrize(
    "leg,path,json_key,table,no_col",
    [
        ("expect", "/api/expects", "doc_no", "expect_lines", "doc_no"),
        ("receive", "/api/receives", "receive_no", "receive_lines", "receive_no"),
        ("confirm", "/api/confirms", "confirm_no", "confirm_lines", "confirm_no"),
    ],
)
def test_concurrent_duplicate_submissions_leave_exactly_one_row(
    client, seeded_db, leg, path, json_key, table, no_col
):
    """N threads submit the same number at once: exactly one row, N-1 rejects."""
    number = {"expect": "EX-RACE", "receive": "RC-RACE", "confirm": "CF-RACE"}[leg]
    body = {json_key: number, "supplier_code": "SUP-A", "sku": "SKU-RACE", "dock_day": DAY, "qty": 6}
    results: list[int] = []
    lock = threading.Lock()

    def submit():
        r = client.post(path, json=body)
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results).count(200) == 1, results
    assert sorted(results).count(409) == len(threads) - 1, results
    assert _count(seeded_db, table, f"{no_col}=?", number) == 1
    assert _sum(seeded_db, table, "SUP-A", "SKU-RACE") == 6.0
    # Every reject was audited; the single success was not.
    rejected = [r for r in _rows(seeded_db, "rejection_log") if r["number"] == number]
    assert len(rejected) == len(threads) - 1

def test_concurrent_duplicate_across_real_http_server(seeded_db):
    """Same policy under uvicorn-style concurrent HTTP (separate connections)."""
    import threading

    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        import time

        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        body = {"confirm_no": "CF-RACE2", "supplier_code": "SUP-B", "sku": "SKU-R2", "dock_day": DAY, "qty": 9}

        statuses: list[int] = []
        lock = threading.Lock()

        def go():
            with httpx.Client(timeout=15) as c:
                r = c.post(f"{base}/api/confirms", json=body)
                with lock:
                    statuses.append(r.status_code)

        ts = [threading.Thread(target=go) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert statuses.count(200) == 1, statuses
        assert statuses.count(409) == 5, statuses
        assert _count(seeded_db, "confirm_lines", "confirm_no=?", "CF-RACE2") == 1
    finally:
        server.should_exit = True
        thread.join(timeout=10)
