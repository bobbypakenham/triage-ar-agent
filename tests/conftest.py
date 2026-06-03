"""
tests/conftest.py — shared pytest fixtures for the Triage test suite.

The `db` fixture provides an isolated, in-memory SQLite database so DB-touching
tests are fast and never read or write the real data/triage.db. It lives here
(rather than in a single test module) so every test file — test_core.py,
test_stress.py — shares one definition.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Make `src` importable regardless of how pytest is invoked. conftest.py is
# imported before any test module, so this runs first.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import database  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated, in-memory SQLite database for fast, fully-isolated DB tests.

    database.get_conn() normally opens a *new* connection on every call. A bare
    ':memory:' path would therefore give each call its own private, empty
    database — writes would never be visible to later reads. So we create ONE
    persistent in-memory connection and monkeypatch get_conn to hand it back
    every time. SQLite's `with conn:` context manager commits (or rolls back)
    but does NOT close, so the single connection safely survives every
    `with get_conn() as conn:` block in the codebase.

    We also chdir into tmp_path so any relative paths (the 'data/' dir, the
    JSON migration source) resolve inside the temp dir and never touch real
    project files. The connection is closed on teardown.
    """
    monkeypatch.chdir(tmp_path)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(database, "get_conn", lambda: conn)

    database.init_db()
    try:
        yield database
    finally:
        conn.close()
