"""Shared pytest fixtures: fresh seeded DB per test session."""
import os
import tempfile

import pytest

from app import config
from app.db import connect, init_schema
from app.seed import seed as seed_db


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("data") / "test.db")
    seed_db(path, force=True)
    return path


@pytest.fixture(scope="session")
def conn(db_path):
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def models(conn):
    from app.forecast import ensure_models
    return ensure_models(conn)


@pytest.fixture()
def client(db_path, monkeypatch):
    """FastAPI test client against the session DB."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(config, "DB_PATH", db_path)
    from app import main
    main._state["models"] = None
    main._state["metrics"] = None
    main._state["accuracy"] = None
    with TestClient(main.app) as c:
        yield c
