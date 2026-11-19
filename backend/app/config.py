from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "Tridock"
API_PORT = 10900

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

def data_dir() -> Path:
    """Data directory, honouring TRIDOCK_DATA (used by the container)."""
    raw = os.environ.get("TRIDOCK_DATA")
    return Path(raw).expanduser() if raw else _DEFAULT_DATA_DIR

def db_path() -> Path:
    """Database file, honouring TRIDOCK_DB (used by tests)."""
    raw = os.environ.get("TRIDOCK_DB")
    if raw:
        return Path(raw).expanduser()
    return data_dir() / "tridock.db"

# Resolved at import time for backwards compatibility. Prefer the functions
# above in code that may run after the environment changes (tests).
DATA_DIR = data_dir()
DB_PATH = db_path()
