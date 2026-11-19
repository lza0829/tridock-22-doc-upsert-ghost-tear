from __future__ import annotations

from app.db import write_session

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);
-- 三腿正式表：单号在本腿内唯一，是「同单号拒重」的最终权威。
CREATE TABLE IF NOT EXISTS expect_lines (
  id INTEGER PRIMARY KEY,
  doc_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receive_lines (
  id INTEGER PRIMARY KEY,
  receive_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirm_lines (
  id INTEGER PRIMARY KEY,
  confirm_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
-- 被拒提交的审计流水（不改动三腿正式表）。
CREATE TABLE IF NOT EXISTS rejection_log (
  id INTEGER PRIMARY KEY,
  leg TEXT NOT NULL,
  number TEXT NOT NULL,
  supplier_code TEXT,
  sku TEXT,
  dock_day TEXT,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejection_log_day ON rejection_log(dock_day, id);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  id INTEGER PRIMARY KEY,
  batch_no TEXT NOT NULL UNIQUE,
  dock_day TEXT NOT NULL,
  matched_count INTEGER NOT NULL,
  variance_count INTEGER NOT NULL,
  status TEXT NOT NULL,          -- open | sealed | stale
  sealed_at TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_batches_day_status ON reconcile_batches(dock_day, status);
CREATE TABLE IF NOT EXISTS match_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL NOT NULL,
  receive_qty REAL NOT NULL,
  confirm_qty REAL NOT NULL,
  aligned INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_match_rows_batch ON match_rows(batch_id);
CREATE TABLE IF NOT EXISTS variance_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL NOT NULL,
  receive_qty REAL NOT NULL,
  confirm_qty REAL NOT NULL,
  fate TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_variance_rows_batch ON variance_rows(batch_id);
CREATE TABLE IF NOT EXISTS late_spill (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS reopen_requests (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'none'
);
'''

def seed_if_empty() -> None:
    """Create the schema and seed the fixture rows once.

    Safe to call from multiple worker processes at startup: the writes run in
    one IMMEDIATE transaction and the post-lock check re-reads the supplier
    count, so a racing process finds the rows already there.
    """
    from app.db import write_session

    with write_session() as conn:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]:
                conn.execute("COMMIT")
                return
            _seed_rows(conn)
            conn.execute("COMMIT")
        except BaseException:
            conn.rollback()
            raise

def _seed_rows(conn) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES ('dock_day', ?)", ("2026-11-18",))
    suppliers = [
        ("SUP-A", "东海食材"),
        ("SUP-B", "北岭包装"),
        ("SUP-C", "南湾百货"),
    ]
    for code, title in suppliers:
        conn.execute("INSERT INTO suppliers(code, title) VALUES (?,?)", (code, title))
    expects = [
        ("EX-1001", "SUP-A", "SKU-米-25", "2026-11-18", 40, "早班预报"),
        ("EX-1002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
        ("EX-1003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
        ("EX-1004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, None),
        ("EX-0901", "SUP-A", "SKU-米-25", "2026-11-17", 10, "昨日"),
    ]
    for row in expects:
        conn.execute(
            '''INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)
               VALUES (?,?,?,?,?,?,?)''',
            (*row, "2026-11-18T08:00:00Z"),
        )
    receives = [
        ("RC-2001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "2026-11-18T09:10:00Z", "少2"),
        ("RC-2002", "SUP-A", "SKU-油-5", "2026-11-18", 20, "2026-11-18T09:20:00Z", None),
        ("RC-2003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, "2026-11-18T10:00:00Z", None),
        ("RC-2004", "SUP-C", "SKU-巾-卷", "2026-11-18", 66, "2026-11-18T11:00:00Z", "多6"),
    ]
    for row in receives:
        conn.execute(
            '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
               VALUES (?,?,?,?,?,?,?,?)''',
            (*row, row[5]),
        )
    confirms = [
        ("CF-3001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "回执按实收"),
        ("CF-3002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
        ("CF-3003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
        ("CF-3004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, "回执仍报预报数"),
    ]
    for row in confirms:
        conn.execute(
            '''INSERT INTO confirm_lines(confirm_no, supplier_code, sku, dock_day, qty, note, created_at)
               VALUES (?,?,?,?,?,?,?)''',
            (*row, "2026-11-18T12:00:00Z"),
        )
