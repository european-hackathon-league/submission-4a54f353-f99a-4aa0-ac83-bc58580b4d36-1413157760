"""Phase 3: signal contract, validation gate, quarantine."""

import datetime as dt

import pytest

from app import audit
from app.signals import service
from app.signals.gate import find_quote

TEXT = "The “seat ventilation” is  far too weak in summer.\nOtherwise I love the car."


@pytest.fixture
def base(conn):
    for code in ("seats", "general", "infotainment"):
        audit.insert(conn, "car_area", {"code": code, "name": code}, actor="seed:test", key="code")
    audit.insert(conn, "source_registry", {"source_id": "reddit", "name": "Reddit"}, actor="seed:test",
                 key="source_id")
    audit.insert(conn, "vehicle", {"code": "G60", "series": "5 Series", "is_current": True,
                                   "on_sale_since": dt.date(2023, 10, 1)}, actor="seed:test", key="code")
    audit.insert(conn, "competitor", {"bmw_vehicle": "G60", "brand": "Mercedes-Benz", "model": "E-Class"},
                 actor="seed:test")
    for rid, published, author in (("reddit-1", dt.datetime(2025, 3, 1, tzinfo=dt.timezone.utc), "a1"),
                                   ("reddit-2", dt.datetime(2022, 3, 1, tzinfo=dt.timezone.utc), "a2"),
                                   ("reddit-3", None, "a1")):
        audit.insert(conn, "source_record", {
            "record_id": rid, "source_id": "reddit", "text_original": TEXT, "content_hash": rid,
            "published_at": published, "author_hash": author, "country": "US", "market": "USA",
        }, actor="agent:feedback", key="record_id")
    return conn


def proposal(**over):
    p = {"record_id": "reddit-1", "kind": "complaint", "severity": "difficult_to_use",
         "summary": "Front seat ventilation too weak in summer", "quote": "seat ventilation” is far too weak",
         "car_area": ["seats"], "feature_hint": "seat ventilation", "vehicle": "G60", "vehicle_role": "current"}
    p.update(over)
    return p


def ingest(conn, *props):
    return service.ingest(conn, list(props), actor="agent:feedback")


def test_find_quote_returns_exact_original_span():
    span = find_quote('"seat ventilation" is far too weak', TEXT)
    assert span == "“seat ventilation” is  far too weak" and span in TEXT
    assert find_quote("heated steering wheel", TEXT) is None


def test_valid_signal_is_stored_with_embedding_and_market(base):
    out = ingest(base, proposal())
    assert out["valid"] == 1
    row = out["rows"][0]
    assert row["quote"] in TEXT and row["market"] == "USA" and row["market_basis"] == "stated"
    assert row["embedding"] is not None and row["extra"]["embedding_provider"].startswith("local")
    assert row["scope_tag"] == "in_scope"


@pytest.mark.parametrize("over, reason", [
    ({"quote": "the heated steering wheel is broken"}, "quote not found"),
    ({"summary": " ".join(["word"] * 21)}, "schema: summary"),
    ({"severity": "none"}, "severity none does not fit"),
    ({"car_area": ["roof"]}, "unknown car area"),
    ({"evidence_role": "competitor"}, "competitor signal without"),
    ({"kind": "competitor_fact", "severity": "none", "record_id": "reddit-3", "quote": "Otherwise I love the car",
      "evidence_role": "market"}, "competitor fact without a date"),
])
def test_gate_quarantines_with_reason(base, over, reason):
    out = ingest(base, proposal(**over))
    assert out["quarantined"] == 1, out
    assert out["rows"][0]["reject_reason"].startswith(reason)


def test_unknown_record_is_dropped_not_stored(base):
    out = ingest(base, proposal(record_id="reddit-404"))
    assert out["dropped"] == 1 and not out["rows"]


def test_duplicates(base):
    assert ingest(base, proposal())["valid"] == 1
    again = ingest(base, proposal())
    assert again["rows"][0]["reject_reason"].startswith("duplicate")
    other_feature = ingest(base, proposal(feature_hint="climate"))
    assert other_feature["valid"] == 1
    cross_post = ingest(base, proposal(record_id="reddit-3"))
    assert cross_post["rows"][0]["reject_reason"].startswith("near-duplicate")


def test_predecessor_role_and_scope_tag(base):
    old = ingest(base, proposal(record_id="reddit-2"))["rows"][0]
    assert old["vehicle_role"] == "predecessor" and "on_sale_since" in old["extra"]["role_reason"]
    priced = ingest(base, proposal(summary="Seat ventilation price too high", feature_hint="vent price"))["rows"][0]
    assert priced["scope_tag"] == "out_of_scope"


def test_delete_quarantined_purges_content_and_record(base):
    q = ingest(base, proposal(record_id="reddit-2", quote="not in text"))["rows"][0]
    service.delete_quarantined(base, q["id"], actor="pm:anna", rationale="spam")
    sig = base.execute("select * from signal where id = %s", (q["id"],)).fetchone()
    assert sig["validation"] == "deleted" and sig["quote"] is None and sig["deleted_at"] is not None
    rec = base.execute("select text_original, deleted_at from source_record where record_id = 'reddit-2'").fetchone()
    assert rec["text_original"] is None and rec["deleted_at"] is not None
    actions = [r["action"] for r in base.execute(
        "select action from audit_event where object_id in (%s, 'reddit-2') order by seq", (str(q["id"]),)).fetchall()]
    assert actions.count("delete") == 2


def test_release_after_correction(base):
    q = ingest(base, proposal(car_area=["roof"]))["rows"][0]
    released = service.release(base, q["id"], {"car_area": ["seats"]}, actor="pm:anna", rationale="area fixed")
    assert released["validation"] == "valid" and released["embedding"] is not None


def test_quarantine_summary_view(base):
    ingest(base, proposal(car_area=["roof"]), proposal(quote="nope", feature_hint="x"))
    rows = base.execute("select reason, signals from signal_quarantine_summary order by reason").fetchall()
    assert {r["reason"]: r["signals"] for r in rows} == {"quote not found in record": 1, "unknown car area roof": 1}
