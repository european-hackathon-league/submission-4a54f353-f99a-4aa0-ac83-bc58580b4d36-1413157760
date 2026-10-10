"""Phase 8: fetch adapters on recorded HTTP responses (no live web in tests)."""

import pathlib

import httpx
import pytest
import respx

from app import audit, db
from app.fetch import agents, worker
from app.fetch.adapters import api, feeds, scrape
from app.fetch.adapters.base import FetchContext
from app.fetch.http import PoliteClient
from app.fetch.subjects import Subject

FIX = pathlib.Path(__file__).parent / "fixtures" / "http"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "raw_local_dir", tmp_path / "raw")
    monkeypatch.setattr(get_settings(), "storage_backend", "local")


def ctx(backfill=False):
    client = PoliteClient(budget=100, default_delay=0, respect_robots=False)
    return FetchContext(client=client, backfill=backfill)


def g60(**over) -> Subject:
    s = Subject(kind="vehicle", id="G60", brand="BMW", model="5 Series", bmw_vehicle="G60", chassis_code="G60",
                vehicle_role="current", markets=["USA", "Europe"],
                terms=[{"term": "BMW i5", "language": "en", "collision_safe": True, "scope": "any"},
                       {"term": "BMW 5 Series", "language": "en", "collision_safe": True, "scope": "any"},
                       {"term": "i5", "language": "en", "collision_safe": False, "scope": "single_brand_source"}])
    for k, v in over.items():
        setattr(s, k, v)
    return s


SOURCE = {"source_id": "nhtsa-complaints", "categories": ["A"], "access": "API", "fetch_step": 2,
          "last_cursor": {}, "delay_seconds": 0}


@respx.mock
def test_nhtsa_complaints_records(monkeypatch):
    monkeypatch.setattr(api, "_model_years", lambda s: [2024])
    respx.get(url__startswith="https://api.nhtsa.gov/products/vehicle/models").mock(
        return_value=httpx.Response(200, content=fixture("nhtsa_models_2024.json")))
    respx.get(url__startswith="https://api.nhtsa.gov/complaints/complaintsByVehicle").mock(
        return_value=httpx.Response(200, content=fixture("nhtsa_complaints_i5_2024.json")))
    out = api.nhtsa_complaints(ctx(), SOURCE, g60(), {})
    assert out.records, "the I5 name must be found in the NHTSA model list"
    r = out.records[0]
    assert r["record_id"].startswith("nhtsa-complaints-") and r["category"] == "A" and r["market"] == "USA"
    assert r["published_at"] is not None and r["raw_ref"].startswith("nhtsa-complaints/")
    again = api.nhtsa_complaints(ctx(), SOURCE, g60(), out.cursor)
    assert again.records == []  # incremental run stops at stored complaints


@respx.mock
def test_app_store_reviews():
    respx.get(url__regex=r"https://itunes\.apple\.com/us/rss/customerreviews/page=1/id=1519457734/.*").mock(
        return_value=httpx.Response(200, content=fixture("appstore_us_mybmw.json")))
    respx.get(url__regex=r"https://itunes\.apple\.com/.*").mock(return_value=httpx.Response(200, json={"feed": {}}))
    source = {"source_id": "app-store", "categories": ["H"]}
    out = feeds.fetch_app_store(ctx(), source, [g60()], {})
    assert len(out.records) == 50
    r = out.records[0]
    assert r["level"] == "brand" and r["rating_scale"] == 5 and r["country"] == "US" and r["author_hash"]
    assert out.cursor["1519457734-us"]


@respx.mock
def test_news_feed_keeps_only_entries_about_the_subject():
    respx.get("https://www.bmwblog.com/feed/").mock(return_value=httpx.Response(200, content=fixture("bmwblog_feed.xml")))
    source = {"source_id": "bmwblog", "categories": ["J"]}
    out = feeds.fetch_feed(ctx(), source, [g60()], {})
    assert all(any(t in r["text_original"] for t in ("i5", "5 Series")) for r in out.records)
    assert all(r["content_type"] == "news" for r in out.records)


def test_xenforo_parsers():
    threads, nxt = scrape.parse_thread_list(fixture("xenforo_list.html").decode("utf-8"), "https://www.genesisowners.com")
    assert len(threads) >= 20 and nxt.endswith("/page-2") and threads[0]["latest"] > 0
    posts, _ = scrape.parse_thread(fixture("xenforo_thread.html").decode("utf-8"), "https://www.genesisowners.com")
    assert len(posts) == 20 and posts[0]["author"] and posts[0]["published"] and posts[0]["text"]


def test_heuristic_agent_extracts_quotes_from_owner_text():
    record = {"record_id": "r1", "source_id": "nhtsa-complaints", "category": "A", "content_type": "owner",
              "language": "en", "vehicle_role": "current", "author_hash": "a1", "engagement": {},
              "extra": {"bmw_vehicle": "G60", "subject_id": "G60"},
              "text_original": "The infotainment screen keeps freezing after the update. Otherwise a nice car."}
    proposals = agents.heuristic_extract(record)
    assert proposals and proposals[0]["quote"] in record["text_original"]
    assert proposals[0]["evidence_role"] == "bmw_customer" and proposals[0]["kind"] == "complaint"


@respx.mock
def test_worker_end_to_end(db_url, monkeypatch):
    monkeypatch.setattr(api, "_model_years", lambda s: [2024])
    respx.get(url__startswith="https://api.nhtsa.gov/products/vehicle/models").mock(
        return_value=httpx.Response(200, content=fixture("nhtsa_models_2024.json")))
    respx.get(url__startswith="https://api.nhtsa.gov/complaints/complaintsByVehicle").mock(
        return_value=httpx.Response(200, content=fixture("nhtsa_complaints_i5_2024.json")))
    with db.transaction() as conn:
        for code in ("infotainment", "driving_dynamics", "powertrain", "driver_assistance", "seats", "climate",
                     "range_charging", "connected_services", "access_cargo", "interior", "exterior", "lighting",
                     "quality", "safety", "general"):
            audit.insert(conn, "car_area", {"code": code, "name": code}, actor="seed:test", key="code")
        audit.insert(conn, "vehicle", {"code": "G60", "series": "5 Series", "is_current": True}, actor="seed:test",
                     key="code")
        audit.insert(conn, "source_registry", {"source_id": "nhtsa-complaints", "name": "NHTSA", "access": "API",
                                               "fetch_step": 2, "enabled": True, "categories": ["A"],
                                               "delay_seconds": 0}, actor="seed:test", key="source_id")
        source = conn.execute("select * from source_registry").fetchone()
    summary = worker.fetch_source(db.transaction, PoliteClient(budget=50, default_delay=0, respect_robots=False),
                                  source, [g60()], False, True)
    if summary["status"] != "ok":
        with db.transaction() as conn:
            raise AssertionError(conn.execute("select error from agent_run").fetchone()["error"])
    assert summary["records"] == 20
    assert summary["signals"]["valid"] > 0
    with db.transaction() as conn:
        bad = conn.execute("select count(*) n from signal s join source_record r using (record_id)"
                           " where s.validation = 'valid' and strpos(r.text_original, s.quote) = 0").fetchone()["n"]
        assert bad == 0
        run = conn.execute("select status, records_fetched from agent_run where agent = 'fetch:nhtsa-complaints'"
                           ).fetchone()
        assert run == {"status": "ok", "records_fetched": 20}
        cursor = conn.execute("select last_cursor from source_registry").fetchone()["last_cursor"]
        assert cursor["G60"]


def test_missing_credentials_are_reported_not_crashed():
    with pytest.raises(Exception) as exc:
        api.reddit(ctx(), {"source_id": "reddit"}, g60(), {})
    assert "REDDIT_CLIENT_ID" in str(exc.value)
