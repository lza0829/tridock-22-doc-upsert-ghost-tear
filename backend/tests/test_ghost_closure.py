"""收口回归：题面四个历史故障夹具在「正式表单据唯一」架构下必须闭合。

夹具：
1. 成功双行 —— 同单号反复打进管道，回执第一次成功、其后一律 409，库内始终一行；
2. 失败却改库 —— 被拒提交不得改动任何正式行数量；
3. 痕迹悬空 —— rejection_log 每条痕迹都对应一次真实被拒事件，正式表无双行；
4. 并发双落 —— 并发同单号只落一行，且被拒次数与痕迹条数同源。

附加联动断言：扫描聚合禁止双计、看板/装载列表与库内行数同源、封日后拒绝再动。
列表一行而库内两行仍能被扫描吃到，即视为本夹具未闭合。
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

DAY = "2026-11-18"

LEGS = [
    (
        "expect",
        "/api/expects",
        "expect_lines",
        "doc_no",
        lambda n: {"doc_no": n},
    ),
    (
        "receive",
        "/api/receives",
        "receive_lines",
        "receive_no",
        lambda n: {"receive_no": n},
    ),
    (
        "confirm",
        "/api/confirms",
        "confirm_lines",
        "confirm_no",
        lambda n: {"confirm_no": n},
    ),
]


def _open(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.parametrize("leg,path,table,no_col,payload", LEGS)
def test_fixture_success_double_row_collapses_to_one(client, seeded_db, leg, path, table, no_col, payload):
    """夹具1 成功双行：同单号 N 次提交只成功一次，库内恰一行，回执与现行行同源。"""
    number = f"{leg.upper()[:2]}-GHOST-1"
    body = {**payload(number), "supplier_code": "SUP-A", "sku": "SKU-G1", "dock_day": DAY, "qty": 10}

    first = client.post(path, json=body)
    assert first.status_code == 200
    receipt = first.json()
    assert receipt["ok"] is True

    statuses = [client.post(path, json={**body, "qty": 99}).status_code for _ in range(4)]
    assert statuses == [409] * 4

    with _open(seeded_db) as conn:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {no_col}=?", (number,)
        ).fetchall()
        assert len(rows) == 1, "同单号在正式表出现双行即未闭合"
        # 回执声称的 id/数量必须与唯一现行行同源，不许回执撒谎。
        assert rows[0]["id"] == receipt["id"]
        assert rows[0]["qty"] == 10.0
        traces = conn.execute(
            "SELECT COUNT(*) c FROM rejection_log WHERE leg=? AND number=?", (leg, number)
        ).fetchone()["c"]
        assert traces == 4, "每次被拒都必须留痕，痕迹数与拒单数同源"


@pytest.mark.parametrize("leg,path,table,no_col,payload", LEGS)
def test_fixture_failure_must_not_mutate_quantity(client, seeded_db, leg, path, table, no_col, payload):
    """夹具2 失败却改库：被拒回执（409）之后，现行行数量与总量保持原样。"""
    number = f"{leg.upper()[:2]}-GHOST-2"
    body = {**payload(number), "supplier_code": "SUP-B", "sku": "SKU-G2", "dock_day": DAY, "qty": 5}
    assert client.post(path, json=body).status_code == 200

    with _open(seeded_db) as conn:
        before = conn.execute(
            f"SELECT COALESCE(SUM(qty),0) s FROM {table} "
            "WHERE dock_day=? AND supplier_code=? AND sku='SKU-G2'",
            (DAY, "SUP-B"),
        ).fetchone()["s"]

    for bad_qty in (500, -3, 0):
        r = client.post(path, json={**body, "qty": abs(bad_qty) or 1})
        assert r.status_code == 409

    with _open(seeded_db) as conn:
        after = conn.execute(
            f"SELECT COALESCE(SUM(qty),0) s FROM {table} "
            "WHERE dock_day=? AND supplier_code=? AND sku='SKU-G2'",
            (DAY, "SUP-B"),
        ).fetchone()["s"]
        kept = conn.execute(
            f"SELECT qty FROM {table} WHERE {no_col}=?", (number,)
        ).fetchall()
    assert after == before == 5.0
    assert len(kept) == 1 and kept[0]["qty"] == 5.0


@pytest.mark.parametrize("leg,path,table,no_col,payload", LEGS)
def test_fixture_no_dangling_trace(client, seeded_db, leg, path, table, no_col, payload):
    """夹具3 痕迹悬空：痕迹表里每条记录都对应一次真实被拒，且正式表绝无双行。"""
    number = f"{leg.upper()[:2]}-GHOST-3"
    body = {**payload(number), "supplier_code": "SUP-C", "sku": "SKU-G3", "dock_day": DAY, "qty": 8}
    assert client.post(path, json=body).status_code == 200
    for _ in range(3):
        assert client.post(path, json=body).status_code == 409

    with _open(seeded_db) as conn:
        # 痕迹不悬空：所有痕迹的单号在正式表最多一行（现行权威），且原因码合法。
        dupes = conn.execute(
            f"""SELECT COUNT(*) c FROM (
                  SELECT {no_col} no FROM {table} GROUP BY {no_col} HAVING COUNT(*) > 1
                )"""
        ).fetchone()["c"]
        assert dupes == 0
        traces = conn.execute(
            "SELECT code, number FROM rejection_log WHERE leg=? AND number=?", (leg, number)
        ).fetchall()
        assert len(traces) == 3
        assert {t["code"] for t in traces} <= {"duplicate_no", "day_sealed", "constraint_violation"}
        # 成功那一笔不留拒重痕迹；痕迹只属于被拒事件。
        for t in traces:
            n = conn.execute(
                f"SELECT COUNT(*) c FROM {table} WHERE {no_col}=?", (t["number"],)
            ).fetchone()["c"]
            assert n == 1, "痕迹指向双行现行数据即悬空"


@pytest.mark.parametrize("leg,path,table,no_col,payload", LEGS)
def test_fixture_concurrent_double_landing(client, seeded_db, leg, path, table, no_col, payload):
    """夹具4 并发双落：N 线程同单号并发，只落一行、无半截行，痕迹 N-1 条。"""
    number = f"{leg.upper()[:2]}-GHOST-4"
    body = {**payload(number), "supplier_code": "SUP-A", "sku": "SKU-G4", "dock_day": DAY, "qty": 6}
    statuses: list[int] = []
    lock = threading.Lock()

    def go():
        r = client.post(path, json=body)
        with lock:
            statuses.append(r.status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert statuses.count(200) == 1
    assert statuses.count(409) == 7
    with _open(seeded_db) as conn:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {no_col}=?", (number,)
        ).fetchall()
        assert len(rows) == 1 and rows[0]["qty"] == 6.0
        assert conn.execute(
            "SELECT COUNT(*) c FROM rejection_log WHERE leg=? AND number=?", (leg, number)
        ).fetchone()["c"] == 7


def test_scan_aggregation_never_double_counts_and_matches_list_and_dashboard(client, seeded_db):
    """扫描聚合/看板/装载列表联动：列表一行、库内一行，扫描恰吃一次。"""
    body = {
        "confirm_no": "CF-SCAN-1",
        "supplier_code": "SUP-A",
        "sku": "SKU-SCAN",
        "dock_day": DAY,
        "qty": 12,
    }
    assert client.post("/api/confirms", json=body).status_code == 200
    for _ in range(3):
        assert client.post("/api/confirms", json=body).status_code == 409

    # 装载列表与库内同源：同单号一行。
    listed = [r for r in client.get("/api/confirms").json() if r["confirm_no"] == "CF-SCAN-1"]
    assert len(listed) == 1

    run = client.post("/api/reconcile/run", json={"dock_day": DAY}).json()
    matches = [
        m
        for m in client.get(f"/api/reconcile/matches?batch_id={run['batch_id']}").json()
        if m["sku"] == "SKU-SCAN"
    ]
    assert len(matches) == 1, "扫描把同单据吃成两行即未闭合"
    assert matches[0]["confirm_qty"] == 12.0

    dash = client.get("/api/dashboard").json()
    with _open(seeded_db) as conn:
        physical = conn.execute(
            "SELECT COUNT(*) c FROM confirm_lines WHERE dock_day=?", (DAY,)
        ).fetchone()["c"]
    assert dash["confirm_count"] == physical
    assert dash["rejected_count"] >= 3


def test_sealed_day_refuses_every_rewrite(client, seeded_db):
    """封日后新单与重复单一律拒绝，库不变，且留 day_sealed 痕迹。"""
    run = client.post("/api/reconcile/run", json={"dock_day": DAY}).json()
    for v in client.get(f"/api/reconcile/variances?batch_id={run['batch_id']}").json():
        client.post(f"/api/reconcile/variances/{v['id']}/fate", json={"fate": "accept_short"})
    assert client.post("/api/reconcile/seal", json={"dock_day": DAY}).status_code == 200

    with _open(seeded_db) as conn:
        before = {
            t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in ("expect_lines", "receive_lines", "confirm_lines")
        }

    fresh = client.post(
        "/api/expects",
        json={"doc_no": "EX-SEALED-NEW", "supplier_code": "SUP-A", "sku": "SKU-S", "dock_day": DAY, "qty": 1},
    )
    replay = client.post(
        "/api/confirms",
        json={"confirm_no": "CF-3001", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": DAY, "qty": 1},
    )
    assert fresh.status_code == 400 and fresh.json()["detail"]["code"] == "day_sealed"
    assert replay.status_code == 400 and replay.json()["detail"]["code"] == "day_sealed"

    with _open(seeded_db) as conn:
        for t, n in before.items():
            assert conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] == n
        codes = {
            r["code"]
            for r in conn.execute(
                "SELECT code FROM rejection_log WHERE dock_day=? AND code='day_sealed'", (DAY,)
            ).fetchall()
        }
    assert codes == {"day_sealed"}
