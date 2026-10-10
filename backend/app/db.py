"""Postgres access: one connection pool, dict rows, pgvector adapters."""

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None
_url_override: str | None = None


def set_database_url(url: str | None) -> None:
    """Point the pool at another database (tests, scripts). Closes the old pool."""
    global _pool, _url_override
    if _pool is not None:
        _pool.close()
        _pool = None
    _url_override = url


def database_url() -> str:
    if _url_override:
        return _url_override
    settings = get_settings()
    if settings.db_url:
        return settings.db_url
    from app import localdb

    return localdb.start(settings.local_db_dir)


def _configure(conn: psycopg.Connection) -> None:
    conn.row_factory = dict_row
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        pass  # extension not created yet (first migration run)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url(), min_size=1, max_size=8, configure=_configure, open=True,
            kwargs={"autocommit": False, "prepare_threshold": None},
        )
    return _pool


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """A connection inside one transaction: commit on success, rollback on error."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def connect(url: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    """A standalone connection (migrations, one-off scripts)."""
    conn = psycopg.connect(url or database_url(), autocommit=autocommit, prepare_threshold=None)
    _configure(conn)
    return conn
