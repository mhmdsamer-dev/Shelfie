"""
conftest.py — Shared fixtures for Shelfie's pytest suite.

Strategy
--------
* An in-memory SQLite DB (StaticPool) is created per-test so every test
  starts with a clean slate.
* shelfie.database.engine and shelfie.scanner.engine are patched to point at
  the in-memory engine BEFORE TestClient starts the ASGI lifespan (which
  triggers on_startup → create_db_and_tables).
* get_session is overridden via app.dependency_overrides so HTTP handlers
  write to the same in-memory DB.
* LIBRARY_PATH is forced to a non-existent path so on_startup never launches
  the watchdog or a scan thread.
"""

import os

# ── Must be set before any shelfie module is imported ─────────────────────────
os.environ.setdefault("LIBRARY_PATH", "/nonexistent_test_library_path")
os.environ.setdefault("SHELFIE_DATA_DIR", os.path.join(os.path.dirname(__file__), ".test_data"))

import pytest
from sqlalchemy import create_engine as _sa_create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel
from fastapi.testclient import TestClient

import shelfie.database as _db_mod
import shelfie.scanner as _sc_mod
from shelfie.database import get_session
from shelfie.main import app


# ── In-memory engine (function-scoped → fresh DB per test) ────────────────────

@pytest.fixture()
def test_engine():
    """
    Create a brand-new in-memory SQLite engine and wire it into the two
    module-level 'engine' globals that scanner.py and database.py own.
    StaticPool guarantees every Session on this engine shares one connection.
    """
    engine = _sa_create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    # Patch module-level engines so Session(engine) calls inside scan_library,
    # remove_book_from_db, watchdog handlers etc. all hit the in-memory DB.
    old_db, _db_mod.engine = _db_mod.engine, engine
    old_sc, _sc_mod.engine = _sc_mod.engine, engine

    yield engine

    # Restore originals
    _db_mod.engine = old_db
    _sc_mod.engine = old_sc


# ── Bare SQLModel session (for direct DB manipulation in tests) ───────────────

@pytest.fixture()
def db_session(test_engine):
    """A Session bound to the in-memory test engine."""
    with Session(test_engine) as session:
        yield session


# ── FastAPI TestClient with DB isolation ──────────────────────────────────────

@pytest.fixture()
def client(test_engine):
    """
    TestClient whose HTTP handlers always use the in-memory test engine.
    dependency_overrides replaces get_session for every request.
    """
    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ── Convenience: a committed Book row ready for use in tests ──────────────────

@pytest.fixture()
def sample_book(db_session):
    """Insert and return a minimal Book so tests don't repeat boilerplate."""
    from shelfie.database import Book

    book = Book(
        file_path="/tmp/fixtures/sample.pdf",
        title="Sample Book",
        file_type="pdf",
        total_pages=200,
        current_page=0,
    )
    db_session.add(book)
    db_session.commit()
    db_session.refresh(book)
    return book
