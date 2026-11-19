from __future__ import annotations

from pathlib import Path

import pytest

@pytest.fixture()
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh database per test, isolated via TRIDOCK_DB."""
    db_file = tmp_path / "tridock-test.db"
    monkeypatch.setenv("TRIDOCK_DB", str(db_file))

    from app import seed

    seed.seed_if_empty()
    return db_file

@pytest.fixture()
def client(seeded_db):
    from fastapi.testclient import TestClient

    from app.main import app

    # The app's lifespan calls seed_if_empty(), which is idempotent, so
    # entering the context manager seeds whichever TRIDOCK_DB is active.
    with TestClient(app) as c:
        yield c
