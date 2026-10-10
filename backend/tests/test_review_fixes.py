"""Review fixes (Devin, phases 0 and 1): atomic audited writes, vector change
detection, write-once raw storage, conninfo handling, weights, case IDs,
non-unique content hash, strict schemas, requirement rank per weight profile."""

import numpy as np
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel

from app import audit, db, storage
from app.config import get_settings
from app.llm.client import UnsupportedSchema, strict_schema

WEIGHTS = {"distinct_customers": 0.5, "severity": 0.5}


def _count(conn, sql_text, *params):
    return conn.execute(sql_text, params).fetchone()["n"]


# --- audit atomicity ---------------------------------------------------------

def test_bad_actor_on_autocommit_leaves_no_row(db_url):
    with db.connect(db_url, autocommit=True) as conn:
        for call in (
            lambda: audit.insert(conn, "car_area", {"code": "x1", "name": "X"}, actor="bob", key="code"),
            lambda: audit.insert_many(conn, "car_area", [{"code": "x2", "name": "X"}], actor="bob", key="code"),
        ):
            with pytest.raises(ValueError):
                call()
        assert _count(conn, "select count(*) n from car_area where code in ('x1', 'x2')") == 0
        audit.insert(conn, "car_area", {"code": "x3", "name": "X"}, actor="seed:test", key="code")
        with pytest.raises(ValueError):
            audit.update(conn, "car_area", {"code": "x3"}, {"name": "Y"}, actor="bob")
        with pytest.raises(ValueError):
            audit.tombstone(conn, "car_area", {"code": "x3"}, ["name"], actor="bob", rationale="r")
        assert conn.execute("select name from car_area where code = 'x3'").fetchone()["name"] == "X"


def test_unknown_column_or_key_rejected_before_mutation(db_url):
    with db.connect(db_url, autocommit=True) as conn:
        with pytest.raises(ValueError):
            audit.insert(conn, "car_area", {"code": "k1", "name": "K"}, actor="seed:test", key="nope")
        assert _count(conn, "select count(*) n from car_area where code = 'k1'") == 0


def test_failing_audit_insert_rolls_back_the_row(db_url, monkeypatch):
    def broken_write(*args, **kwargs):
        raise RuntimeError("audit store down")

    with db.connect(db_url, autocommit=True) as conn:
        monkeypatch.setattr(audit, "write", broken_write)
        with pytest.raises(RuntimeError):
            audit.insert(conn, "car_area", {"code": "f1", "name": "F"}, actor="seed:test", key="code")
        assert _count(conn, "select count(*) n from car_area where code = 'f1'") == 0
        monkeypatch.undo()

        audit.insert(conn, "car_area", {"code": "f2", "name": "F"}, actor="seed:test", key="code")
        monkeypatch.setattr(audit, "write", broken_write)
        with pytest.raises(RuntimeError):
            audit.update(conn, "car_area", {"code": "f2"}, {"name": "G"}, actor="pm:anna")
        assert conn.execute("select name from car_area where code = 'f2'").fetchone()["name"] == "F"


def test_failing_bulk_audit_rolls_back_all_batches(db_url):
    with db.connect(db_url, autocommit=True) as conn:
        # Batches 1 and 2 are stored and audited, batch 3 fails: nothing may stay.
        rows = [{"code": f"b{i}", "name": "B"} for i in range(4)] + [{"code": "b0", "name": "dup"}]
        with pytest.raises(psycopg.errors.UniqueViolation):
            audit.insert_many_returning(conn, "car_area", rows, actor="seed:test", key="code", batch_size=2)
        assert _count(conn, "select count(*) n from audit_event where object_type = 'car_area'") == 0
        assert _count(conn, "select count(*) n from car_area where code like 'b%%'") == 0


def test_inside_caller_transaction_failure_is_a_savepoint(conn):
    audit.insert(conn, "car_area", {"code": "s1", "name": "S"}, actor="seed:test", key="code")
    with pytest.raises(psycopg.Error):
        audit.insert(conn, "car_area", {"code": "s2", "name": "S"}, actor="seed:test", key="code",
                     run_id="not-a-uuid")
    # The caller's transaction is still usable and keeps the first row.
    assert _count(conn, "select count(*) n from car_area where code in ('s1', 's2')") == 1


def test_idle_non_autocommit_connection_keeps_commit_with_caller(db_url):
    with db.connect(db_url) as conn:
        audit.insert(conn, "car_area", {"code": "c1", "name": "C"}, actor="seed:test", key="code")
        conn.rollback()
        assert _count(conn, "select count(*) n from car_area where code = 'c1'") == 0


# --- vector change detection -------------------------------------------------

def test_replacing_an_embedding_is_detected_and_audited(conn):
    audit.insert(conn, "car_area", {"code": "seats", "name": "Seats"}, actor="seed:test", key="code")
    dim = get_settings().embedding_dim
    a = np.zeros(dim, dtype=np.float32)
    a[0] = 1.0
    b = np.zeros(dim, dtype=np.float32)
    b[1] = 1.0
    feature = audit.insert(conn, "feature", {"name": "Seat ventilation", "car_area": "seats", "embedding": a},
                           actor="seed:test")
    audit.update(conn, "feature", {"id": feature["id"]}, {"embedding": b}, actor="agent:embed")
    stored = conn.execute("select embedding from feature where id = %s", (feature["id"],)).fetchone()["embedding"]
    assert np.array_equal(stored.to_numpy() if hasattr(stored, "to_numpy") else np.asarray(stored), b)
    events = conn.execute("select action from audit_event where object_type = 'feature' and object_id = %s",
                          (str(feature["id"]),)).fetchall()
    assert [e["action"] for e in events] == ["create", "update"]
    # Same vector again (as a list): no change, no event.
    audit.update(conn, "feature", {"id": feature["id"]}, {"embedding": b.tolist()}, actor="agent:embed")
    assert _count(conn, "select count(*) n from audit_event where object_type = 'feature' and object_id = %s",
                  str(feature["id"])) == 2


# --- write-once raw storage --------------------------------------------------

@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    settings = get_settings()  # lru_cached: patch the cached instance
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "raw_local_dir", tmp_path)
    return tmp_path


def test_storage_put_is_write_once(local_storage):
    assert storage.put("src/2026-10-10/a.json", b"one") == "src/2026-10-10/a.json"
    storage.put("src/2026-10-10/a.json", b"one")  # identical bytes: idempotent
    with pytest.raises(storage.RawRecordConflict):
        storage.put("src/2026-10-10/a.json", b"two")
    assert storage.get("src/2026-10-10/a.json") == b"one"


def test_storage_rejects_escaping_ref(local_storage):
    with pytest.raises(ValueError):
        storage.put("../outside.json", b"x")


# --- conninfo handling -------------------------------------------------------

@pytest.mark.parametrize("uri", [
    "postgresql://postgres:@127.0.0.1:5432/postgres",
    "postgresql://postgres@/postgres?host=/tmp/pgsock&port=5432",
    "postgresql:///postgres?host=%2Ftmp%2Fpgsock",
    "host=/tmp/pgsock port=5432 user=postgres dbname=postgres",
])
def test_with_database_switches_only_the_database(uri):
    before = conninfo_to_dict(uri)
    after = conninfo_to_dict(db.with_database(uri, "t_abc"))
    assert after["dbname"] == "t_abc"
    assert {k: v for k, v in after.items() if k != "dbname"} == {k: v for k, v in before.items() if k != "dbname"}


# --- weights -----------------------------------------------------------------

def _config(conn):
    audit.insert(conn, "vehicle", {"code": "G60", "series": "5 Series", "segment": "business", "is_current": True},
                 actor="seed:test", key="code")
    audit.insert(conn, "source_registry", {"source_id": "bmw_sales", "name": "BMW sales", "access": "Internal"},
                 actor="seed:test", key="source_id")
    audit.insert(conn, "data_record", {
        "record_id": "bmw_sales-G60-USA-2030", "source_id": "bmw_sales", "chassis_code": "G60", "market": "USA",
        "period": "2030 forecast", "metric": "sales units", "value": 80000, "unit": "units",
    }, actor="seed:test", key="record_id")
    return audit.insert(conn, "config", {"name": "G60 / USA", "vehicle": "G60", "market": "USA"}, actor="seed:test")


@pytest.mark.parametrize("weights", [
    {"a": 0, "b": 0},
    {},
    {"a": -0.1, "b": 1},
    {"a": "NaN"},
    {"a": "0.5"},
    {"a": None},
])
def test_invalid_weights_rejected(conn, weights):
    cfg = _config(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        audit.insert(conn, "weight_profile", {"config_id": cfg["id"], "name": "bad", "case_weights": weights,
                                              "created_by": "pm:anna"}, actor="pm:anna")


def test_valid_weights_accepted(conn):
    cfg = _config(conn)
    wp = audit.insert(conn, "weight_profile", {"config_id": cfg["id"], "name": "ok",
                                               "case_weights": {"a": 0, "b": 2}, "created_by": "pm:anna"},
                      actor="pm:anna")
    assert wp["case_weights"] == {"a": 0, "b": 2}


# --- case IDs ----------------------------------------------------------------

def test_case_ids_past_9999_do_not_collide(conn):
    conn.execute("select setval('case_number_seq', 9998)")
    ids = [conn.execute("select next_case_id() as id").fetchone()["id"] for _ in range(3)]
    assert ids == ["CASE-9999", "CASE-10000", "CASE-10001"]
    conn.execute("select setval('case_number_seq', 999)")
    assert conn.execute("select next_case_id() as id").fetchone()["id"] == "CASE-1000"


# --- content hash ------------------------------------------------------------

def test_identical_text_from_distinct_records_is_kept(conn):
    audit.insert(conn, "source_registry", {"source_id": "reddit", "name": "Reddit"}, actor="seed:test",
                 key="source_id")
    for rid in ("reddit-1", "reddit-2"):
        audit.insert(conn, "source_record", {"record_id": rid, "source_id": "reddit", "text_original": "Great car",
                                             "content_hash": "same-hash"}, actor="agent:feedback", key="record_id")
    assert _count(conn, "select count(*) n from source_record where content_hash = 'same-hash'") == 2
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            audit.insert(conn, "source_record", {"record_id": "reddit-1", "source_id": "reddit",
                                                 "text_original": "x", "content_hash": "h"},
                         actor="agent:feedback", key="record_id")
    idx = conn.execute("select indexdef from pg_indexes where indexname = 'source_record_content_hash'").fetchone()
    assert idx and "unique" not in idx["indexdef"].lower()


# --- requirement rank per weight profile -------------------------------------

def test_requirement_rank_is_per_weight_profile(conn):
    cfg = _config(conn)
    profiles = {}
    for name, cost, default in (("balanced", {"S": 1.0, "M": 0.85, "L": 0.7}, True),
                                ("budget", {"S": 1.0, "M": 0.7, "L": 0.4}, False)):
        profiles[name] = audit.insert(conn, "weight_profile", {
            "config_id": cfg["id"], "name": name, "case_weights": WEIGHTS, "cost_multipliers": cost,
            "is_default": default, "created_by": "seed:test"}, actor="seed:test")
    audit.insert(conn, "car_area", {"code": "seats", "name": "Seats"}, actor="seed:test", key="code")
    feature = audit.insert(conn, "feature", {"name": "Seat ventilation", "car_area": "seats"}, actor="seed:test")
    case = audit.insert(conn, "case_item", {"feature_id": feature["id"], "title": "Ventilation weak",
                                            "created_by": "agent:cases"}, actor="agent:cases")
    for name, score in (("balanced", 0.8), ("budget", 0.6)):
        audit.insert(conn, "case_score", {"case_id": case["id"], "weight_profile_id": profiles[name]["id"],
                                          "factors": {}, "brand_multiplier": 1.0, "score": score,
                                          "rationale": "test"}, actor="agent:scorer")
    audit.insert(conn, "requirement", {"id": "REQ-G60-0001", "case_id": case["id"], "vehicle": "G60",
                                       "cost_size": "L"}, actor="agent:requirements")
    rows = conn.execute("select weight_profile_name, rank_score from requirement_rank"
                        " where requirement_id = 'REQ-G60-0001' order by weight_profile_name").fetchall()
    got = {r["weight_profile_name"]: float(r["rank_score"]) for r in rows}
    assert got == pytest.approx({"balanced": 0.8 * 0.7, "budget": 0.6 * 0.4})


# --- strict schema -----------------------------------------------------------

class _OpenDict(BaseModel):
    labels: dict[str, str]


class _PlainDict(BaseModel):
    extra: dict


class _Nested(BaseModel):
    title: str
    default: int


class _Outer(BaseModel):
    items: list[_Nested]


@pytest.mark.parametrize("model", [_OpenDict, _PlainDict])
def test_strict_schema_rejects_open_dicts(model):
    with pytest.raises(UnsupportedSchema):
        strict_schema(model)


def test_strict_schema_keeps_fields_named_like_keywords():
    schema = strict_schema(_Outer)
    nested = schema["$defs"]["_Nested"]
    assert set(nested["properties"]) == {"title", "default"}
    assert nested["required"] == ["title", "default"] and nested["additionalProperties"] is False
