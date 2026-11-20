from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from app import config

# SQLite allows one writer at a time. Serialising writes in-process keeps the
# read-check-then-insert sequences atomic without ever relying on the caller,
# while BEGIN IMMEDIATE + busy_timeout covers other processes / workers.
_WRITE_LOCK = threading.RLock()

def connect() -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    # WAL lets readers proceed while a writer holds the write lock.
    conn.execute("PRAGMA journal_mode=WAL")
    # Transaction boundaries are explicit (see transaction()), so that a
    # "check duplicate then insert" pair is atomic.
    conn.isolation_level = None
    return conn

@contextmanager
def write_session():
    """A connection held under the process-wide write lock."""
    with _WRITE_LOCK:
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()

@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT, rolling back on any error.

    IMMEDIATE takes the write lock up front: two concurrent submissions of the
    same number serialise here, so the second one sees the first one's row and
    is rejected instead of both racing past a read-then-insert check.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
