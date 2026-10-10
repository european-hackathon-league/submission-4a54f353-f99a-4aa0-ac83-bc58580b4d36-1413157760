"""The single write path. Every change to a table goes through these helpers,
which write the change and its audit_event in the same transaction.

Actors: ``seed:<script>``, ``agent:<name>``, ``pm:<user>``, ``engineer:<user>``,
``system``. The actor type (ai, human, system) is derived from the prefix.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Mapping, Sequence

import numpy as np
from psycopg import Connection, sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

# Table name -> object_type used in audit_event, decision and the API.
OBJECT_TYPES = {"case_item": "case"}

VECTOR_COLUMNS = {"embedding"}


class NotFound(LookupError):
    pass


class StaleVersion(RuntimeError):
    """Raised when a page save carries an outdated body_version."""


def actor_type(actor: str) -> str:
    prefix = actor.split(":", 1)[0]
    if prefix in ("pm", "engineer", "admin"):
        return "human"
    if prefix in ("agent", "llm"):
        return "ai"
    if prefix in ("seed", "system", "fetch"):
        return "system"
    raise ValueError(f"unknown actor {actor!r}; use seed:, agent:, pm:, engineer:, system")


def object_type_of(table: str) -> str:
    return OBJECT_TYPES.get(table, table)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, np.ndarray):
        return f"<vector {value.shape[0]}>"
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "to_list"):  # pgvector Vector
        return "<vector>"
    return value


def _audit_row(row: Mapping[str, Any] | None) -> dict | None:
    if row is None:
        return None
    return {k: ("<vector>" if k in VECTOR_COLUMNS and v is not None else _json_safe(v)) for k, v in row.items()}


_COLUMN_TYPES: dict[str, dict[str, str]] = {}


def column_types(conn: Connection, table: str) -> dict[str, str]:
    """Column -> Postgres type name (udt_name), cached per table."""
    if table not in _COLUMN_TYPES:
        rows = conn.execute(
            "select column_name, udt_name from information_schema.columns"
            " where table_schema = 'public' and table_name = %s",
            (table,),
        ).fetchall()
        if not rows:
            return {}  # not cached: the table may be created by a later migration
        _COLUMN_TYPES[table] = {r["column_name"]: r["udt_name"] for r in rows}
    return _COLUMN_TYPES[table]


def _same(udt: str, column: str, old: Any, new: Any) -> bool:
    """Real equality for change detection (vectors compared element-wise, never redacted)."""
    if old is None or new is None:
        return old is None and new is None
    if udt == "vector" or column in VECTOR_COLUMNS:
        def arr(v: Any) -> np.ndarray:
            if hasattr(v, "to_numpy"):  # pgvector Vector
                v = v.to_numpy()
            return np.asarray(v, dtype=np.float32)

        return bool(np.array_equal(arr(old), arr(new)))
    return _json_safe(old) == _json_safe(new)


def _check_columns(conn: Connection, table: str, columns: Iterable[str]) -> dict[str, str]:
    """Validate table and column names before any mutation; returns the column types."""
    types = column_types(conn, table)
    if not types:
        raise ValueError(f"unknown table {table!r}")
    unknown = [c for c in columns if c not in types]
    if unknown:
        raise ValueError(f"{table} has no column(s) {unknown}")
    return types


def _key_columns(key: str | Sequence[str]) -> list[str]:
    return [key] if isinstance(key, str) else list(key)


@contextmanager
def atomic(conn: Connection) -> Iterator[None]:
    """Mutation + audit event as one unit.

    Autocommit connection: its own transaction (BEGIN ... COMMIT). Inside a
    transaction: a savepoint, so a failure undoes both and the caller decides.
    An idle non-autocommit connection gets its implicit transaction opened
    first, so the commit stays with the caller.
    """
    if not conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE:
        conn.execute("select 1")
    with conn.transaction():
        yield


def _db_value(types: Mapping[str, str], column: str, value: Any) -> Any:
    if value is None:
        return None
    udt = types.get(column, "")
    if udt == "vector":
        return value if isinstance(value, np.ndarray) else np.asarray(value, dtype=np.float32)
    if udt in ("jsonb", "json"):
        return Jsonb(_json_safe(value))
    return value


def write(
    conn: Connection,
    *,
    action: str,
    object_type: str,
    object_id: str,
    actor: str,
    rationale: str | None = None,
    before: Mapping | None = None,
    after: Mapping | None = None,
    run_id: str | uuid.UUID | None = None,
    request_id: str | None = None,
) -> int:
    """Append one audit event; returns its seq."""
    a_type = actor_type(actor)
    row = conn.execute(
        "insert into audit_event (actor, actor_type, action, object_type, object_id,"
        " before, after, rationale, run_id, request_id)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning seq",
        (
            actor, a_type, action, object_type, str(object_id),
            Jsonb(_audit_row(before)) if before is not None else None,
            Jsonb(_audit_row(after)) if after is not None else None,
            rationale, run_id, request_id,
        ),
    ).fetchone()
    return row["seq"]


def _key_of(row: Mapping[str, Any], key: str | Sequence[str]) -> str:
    if isinstance(key, str):
        return str(row[key])
    return "/".join(str(row[k]) for k in key)


def insert(
    conn: Connection,
    table: str,
    row: Mapping[str, Any],
    *,
    actor: str,
    rationale: str | None = None,
    key: str | Sequence[str] = "id",
    run_id=None,
    request_id: str | None = None,
    on_conflict_do_nothing: bool = False,
) -> dict | None:
    """Insert one row and audit it. Returns the stored row (None if skipped on conflict)."""
    actor_type(actor)  # validate before any mutation
    cols = list(row.keys())
    types = _check_columns(conn, table, [*cols, *_key_columns(key)])
    query = sql.SQL("insert into {} ({}) values ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    if on_conflict_do_nothing:
        query += sql.SQL(" on conflict do nothing")
    query += sql.SQL(" returning *")
    with atomic(conn):
        stored = conn.execute(query, [_db_value(types, c, row[c]) for c in cols]).fetchone()
        if stored is None:
            return None
        write(
            conn, action="create", object_type=object_type_of(table), object_id=_key_of(stored, key),
            actor=actor, rationale=rationale, after=stored, run_id=run_id, request_id=request_id,
        )
    return stored


def insert_many(
    conn: Connection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    actor: str,
    rationale: str | None = None,
    key: str | Sequence[str] = "id",
    run_id=None,
    on_conflict_do_nothing: bool = False,
) -> int:
    """Bulk insert (seed, fetch) with one audit event per stored row. Returns rows stored.

    Rows are written in batches (multi-row INSERT ... RETURNING, then one
    multi-row audit insert per batch) in the caller's transaction.
    """
    return len(insert_many_returning(
        conn, table, rows, actor=actor, rationale=rationale, key=key, run_id=run_id,
        on_conflict_do_nothing=on_conflict_do_nothing,
    ))


def insert_many_returning(
    conn: Connection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    actor: str,
    rationale: str | None = None,
    key: str | Sequence[str] = "id",
    run_id=None,
    on_conflict_do_nothing: bool = False,
    batch_size: int = 500,
) -> list[dict]:
    """Like insert_many but returns the stored rows. The whole call is one atomic unit."""
    a_type = actor_type(actor)  # validate before any mutation
    types = _check_columns(conn, table, _key_columns(key))
    stored_all: list[dict] = []
    batch: list[Mapping[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        cols = list(batch[0].keys())
        values = sql.SQL(", ").join(
            sql.SQL("({})").format(sql.SQL(", ").join(sql.Placeholder() * len(cols))) for _ in batch
        )
        query = sql.SQL("insert into {} ({}) values ").format(
            sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols))
        ) + values
        if on_conflict_do_nothing:
            query += sql.SQL(" on conflict do nothing")
        query += sql.SQL(" returning *")
        params = [_db_value(types, c, r.get(c)) for r in batch for c in cols]
        stored = conn.execute(query, params).fetchall()
        if stored:
            audit_values = sql.SQL(", ").join(sql.SQL("(%s, %s, %s, %s, %s, %s, %s, %s)") for _ in stored)
            audit_params: list[Any] = []
            for s in stored:
                audit_params += [actor, a_type, "create", object_type_of(table), _key_of(s, key),
                                 Jsonb(_audit_row(s)), rationale, run_id]
            conn.execute(
                sql.SQL("insert into audit_event (actor, actor_type, action, object_type, object_id, after,"
                        " rationale, run_id) values ") + audit_values,
                audit_params,
            )
        stored_all.extend(stored)
        batch.clear()

    with atomic(conn):
        for row in rows:
            if not batch or list(row.keys()) != list(batch[0].keys()):
                flush()
                _check_columns(conn, table, row.keys())
            batch.append(row)
            if len(batch) >= batch_size:
                flush()
        flush()
    return stored_all


def _where(key: Mapping[str, Any]) -> tuple[sql.Composed, list]:
    parts = [sql.SQL("{} = %s").format(sql.Identifier(k)) for k in key]
    return sql.SQL(" and ").join(parts), list(key.values())


def fetch_for_update(conn: Connection, table: str, key: Mapping[str, Any]) -> dict:
    where, params = _where(key)
    row = conn.execute(
        sql.SQL("select * from {} where {} for update").format(sql.Identifier(table), where), params
    ).fetchone()
    if row is None:
        raise NotFound(f"{table} {dict(key)} not found")
    return row


def update(
    conn: Connection,
    table: str,
    key: Mapping[str, Any],
    changes: Mapping[str, Any],
    *,
    actor: str,
    rationale: str | None = None,
    action: str = "update",
    expected_version: int | None = None,
    version_column: str = "body_version",
    run_id=None,
    request_id: str | None = None,
) -> dict:
    """Update one row and audit the changed fields (before/after).

    With ``expected_version`` the update only succeeds if the stored version
    matches (stale save check) and the version is incremented.
    """
    actor_type(actor)  # validate before any mutation
    if not key:
        raise ValueError("update needs a non-empty key")
    changes = dict(changes)
    if expected_version is not None:
        changes[version_column] = expected_version + 1
    types = _check_columns(conn, table, [*key, *changes])
    with atomic(conn):
        before = fetch_for_update(conn, table, key)
        if expected_version is not None and before[version_column] != expected_version:
            raise StaleVersion(
                f"{table} {dict(key)} is at version {before[version_column]}, save was based on {expected_version}"
            )
        # Compare real values; the audit payload redacts vectors, so it cannot be used here.
        changed = {c: v for c, v in changes.items() if not _same(types.get(c, ""), c, before.get(c), v)}
        if not changed:
            return before
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in changed)
        where, params = _where(key)
        after = conn.execute(
            sql.SQL("update {} set {} where {} returning *").format(sql.Identifier(table), sets, where),
            [_db_value(types, c, v) for c, v in changed.items()] + params,
        ).fetchone()
        write(
            conn, action=action, object_type=object_type_of(table),
            object_id="/".join(str(v) for v in key.values()),
            actor=actor, rationale=rationale,
            before={c: before.get(c) for c in changed}, after={c: after.get(c) for c in changed},
            run_id=run_id, request_id=request_id,
        )
    return after


def tombstone(
    conn: Connection,
    table: str,
    key: Mapping[str, Any],
    content_columns: Sequence[str],
    *,
    actor: str,
    rationale: str,
    extra_changes: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> dict:
    """Delete = null the content, set deleted_at, keep the row, audit it (atomic, via update)."""
    actor_type(actor)  # validate before any mutation
    changes: dict[str, Any] = {c: None for c in content_columns}
    changes["deleted_at"] = dt.datetime.now(dt.timezone.utc)
    changes.update(extra_changes or {})
    return update(
        conn, table, key, changes, actor=actor, rationale=rationale, action="delete", request_id=request_id
    )


def log_access(
    conn: Connection, *, action: str, object_type: str, object_id: str, actor: str, request_id: str | None = None
) -> int:
    """Views and exports are logged too."""
    return write(
        conn, action=action, object_type=object_type, object_id=object_id, actor=actor, request_id=request_id
    )


def to_json(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False)
