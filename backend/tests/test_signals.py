"""Phase 3: signal contract, validation gate, quarantine."""

import datetime as dt

import pytest

from app import audit
from app.signals import service
from app.signals.gate import find_quote

VENT = "seating_ventilate_heat"
TEXT = "The “seat ventilation” is  far too weak in summer.\nOtherwise I love the car."


@pytest.fixture
def base(conn):
    for code in ("seats", "climate"):
        audit.insert(conn, "car_area", {"code": code, "name": code}, actor="seed:test", key="code")
    for code in ("comfort", "performance", "price"):
        audit.insert(conn, "aspect_group", {"code": code}, actor="seed:test", key="code")
    for topic, area in ((VENT, "seats"), ("vehicle_climatization", "climate"), ("other", None)):
        audit.insert(conn, "feature", {"name": topic, "topic_id": topic, "car_area": area, "status": "confirmed",
                                       "taxonomy_version": "test"}, actor="seed:test")
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
         "topic_id": VENT, "aspect_group": "performance", "vehicle": "G60", "vehicle_role": "current"}
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
    # The car area is derived from the topic, never sent by the agent.
    assert row["car_area"] == ["seats"] and row["aspect_group"] == "performance"
    assert row["classified_by"] == "llm" and row["taxonomy_version"] == "test"
    topic = base.execute("select topic_id from feature where id = %s", (row["feature_id"],)).fetchone()["topic_id"]
    assert topic == VENT


@pytest.mark.parametrize("over, reason", [
    ({"quote": "the heated steering wheel is broken"}, "quote not found"),
    ({"summary": " ".join(["word"] * 21)}, "schema: summary"),
    ({"severity": "none"}, "severity none does not fit"),
    ({"topic_id": "roof_rack"}, "unknown topic roof_rack"),
    ({"aspect_group": "vibes"}, "unknown aspect group vibes"),
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
    other_topic = ingest(base, proposal(topic_id="vehicle_climatization"))
    assert other_topic["valid"] == 1
    cross_post = ingest(base, proposal(record_id="reddit-3"))
    assert cross_post["rows"][0]["reject_reason"].startswith("near-duplicate")


def test_predecessor_role_and_scope_tag(base):
    old = ingest(base, proposal(record_id="reddit-2"))["rows"][0]
    assert old["vehicle_role"] == "predecessor" and "on_sale_since" in old["extra"]["role_reason"]
    priced = ingest(base, proposal(summary="Seat ventilation price too high",
                                   topic_id="vehicle_climatization"))["rows"][0]
    assert priced["scope_tag"] == "out_of_scope"


def test_aspect_group_price_is_out_of_scope_but_stored(base):
    row = ingest(base, proposal(aspect_group="price"))["rows"][0]
    assert row["validation"] == "valid" and row["scope_tag"] == "out_of_scope"


def test_other_and_low_confidence_go_to_the_review_queue(base):
    ingest(base, proposal(topic_id="other", extra={"other_suggestion": "seat fan noise"}))
    ingest(base, proposal(record_id="reddit-2", classification_confidence="low"))
    queue = base.execute("select reason, suggested_name, topic_id from taxonomy_review order by reason").fetchall()
    assert queue == [{"reason": "low_confidence", "suggested_name": None, "topic_id": VENT},
                     {"reason": "other", "suggested_name": "seat fan noise", "topic_id": "other"}]
    # The fallback never becomes a topic: the feature list is unchanged.
    assert base.execute("select count(*) n from feature").fetchone()["n"] == 3


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
    q = ingest(base, proposal(topic_id="roof_rack"))["rows"][0]
    released = service.release(base, q["id"], {"topic_id": VENT, "classified_by": "pm"}, actor="pm:anna",
                               rationale="topic fixed")
    assert released["validation"] == "valid" and released["embedding"] is not None
    assert released["car_area"] == ["seats"] and released["classified_by"] == "pm"


def test_quarantine_summary_view(base):
    ingest(base, proposal(topic_id="roof_rack"), proposal(quote="nope", topic_id="vehicle_climatization"))
    rows = base.execute("select reason, signals from signal_quarantine_summary order by reason").fetchall()
    assert {r["reason"]: r["signals"] for r in rows} == {"quote not found in record": 1, "unknown topic roof_rack": 1}
