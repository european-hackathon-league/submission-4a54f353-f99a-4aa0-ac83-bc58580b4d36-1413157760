"""Test databases: one migrated template on the embedded local Postgres,
cloned into a fresh database per test that needs one."""

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from app import db, localdb, migrate
from app.config import BACKEND_DIR
from app.llm import client as llm_client
from app.llm import embeddings

TEST_PG_DIR = BACKEND_DIR / ".data" / "pg-test"
TEMPLATE = "bmw_test_template"


def _admin(uri: str) -> psycopg.Connection:
    return psycopg.connect(uri, autocommit=True)


def _with_db(uri: str, name: str) -> str:
    return db.with_database(uri, name)


@pytest.fixture(scope="session")
def pg_uri() -> str:
    uri = os.environ.get("TEST_DATABASE_URL") or localdb.start(Path(TEST_PG_DIR))
    with _admin(uri) as conn:
        localdb.create_supabase_roles(conn)
        conn.execute(f"drop database if exists {TEMPLATE}")
        conn.execute(f"create database {TEMPLATE}")
    migrate.apply_all(_with_db(uri, TEMPLATE), verbose=False)
    return uri


@pytest.fixture
def db_url(pg_uri):
    """A fresh, fully migrated database; the app pool points at it."""
    name = "t_" + uuid.uuid4().hex[:12]
    with _admin(pg_uri) as conn:
        conn.execute(f"create database {name} template {TEMPLATE}")
    url = _with_db(pg_uri, name)
    db.set_database_url(url)
    yield url
    db.set_database_url(None)
    with _admin(pg_uri) as conn:
        conn.execute(f"drop database if exists {name} with (force)")


@pytest.fixture
def conn(db_url):
    with db.connect(db_url) as c:
        yield c
        c.rollback()


@pytest.fixture(autouse=True, scope="session")
def offline_settings(tmp_path_factory):
    """Tests never touch the configured Supabase project or OpenAI, whatever backend/.env says:
    raw records go to a temporary folder, the API runs in dev auth mode."""
    from app.config import get_settings

    s = get_settings()
    saved = (s.storage_backend, s.raw_local_dir, s.auth_mode, s.openai_api_key)
    s.storage_backend, s.raw_local_dir = "local", tmp_path_factory.mktemp("raw-records")
    s.auth_mode, s.openai_api_key = "dev", ""
    yield
    s.storage_backend, s.raw_local_dir, s.auth_mode, s.openai_api_key = saved


@pytest.fixture(autouse=True)
def offline_ai():
    """No test talks to OpenAI: local embeddings, no LLM unless a test sets FakeLLM."""
    embeddings.set_embedder(embeddings.LocalEmbedder())
    llm_client.set_llm(None)
    yield
    embeddings.set_embedder(None)
    llm_client.set_llm(None)


SEEDED = "bmw_test_seeded"


@pytest.fixture(scope="session")
def seeded_template(pg_uri):
    """A database seeded once from the provided data (about 1.5 minutes)."""
    with _admin(pg_uri) as conn:
        conn.execute(f"drop database if exists {SEEDED} with (force)")
        conn.execute(f"create database {SEEDED} template {TEMPLATE}")
    from app.seed import run

    embeddings.set_embedder(embeddings.LocalEmbedder())
    db.set_database_url(_with_db(pg_uri, SEEDED))
    try:
        run.seed_all()
    finally:
        db.set_database_url(None)
    return SEEDED


@pytest.fixture
def seeded_url(pg_uri, seeded_template):
    name = "s_" + uuid.uuid4().hex[:12]
    with _admin(pg_uri) as conn:
        conn.execute(f"create database {name} template {seeded_template}")
    url = _with_db(pg_uri, name)
    db.set_database_url(url)
    yield url
    db.set_database_url(None)
    with _admin(pg_uri) as conn:
        conn.execute(f"drop database if exists {name} with (force)")


@pytest.fixture
def sconn(seeded_url):
    with db.connect(seeded_url) as c:
        yield c
        c.rollback()
