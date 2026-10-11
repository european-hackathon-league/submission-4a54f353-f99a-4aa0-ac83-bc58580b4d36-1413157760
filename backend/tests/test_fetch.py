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


# ---- adapters added with the sites module (recorded pages, scripts and styles stripped) ----

ARTICLE = ("<html><head><meta property='article:published_time' content='2026-09-01T10:00:00Z'></head><body>"
           "<article><h1>Test</h1>" + "<p>" + "Der neue BMW 5er im Test: die Sitzbelüftung ist zu schwach. " * 12
           + "</p></article></body></html>")


def test_date_parsers():
    import datetime as dt

    from app.fetch.adapters import sites

    today = dt.date(2026, 10, 11)
    assert sites.german_date("am 10. Oktober 2026 um 10:55 Uhr", today) == dt.datetime(2026, 10, 10, 10, 55,
                                                                                     tzinfo=dt.timezone.utc)
    assert sites.german_date("gestern um 08:00 Uhr", today).date() == dt.date(2026, 10, 10)
    assert sites.english_date("Monday 14th April 2025", today).date() == dt.date(2025, 4, 14)
    assert sites.english_date("Monday 5th October", today).date() == dt.date(2026, 10, 5)
    assert sites.english_date("Yesterday (22:15)", today).date() == dt.date(2026, 10, 10)
    assert sites.english_date("Sep 20 2016", today).date() == dt.date(2016, 9, 20)


@respx.mock
def test_motor_talk_posts():
    from app.fetch.adapters import sites

    respx.get(url__regex=r"https://www\.motor-talk\.de/forum/bmw-5er-g60-g61-g90-g99-b1172\.html.*").mock(
        return_value=httpx.Response(200, content=fixture("motortalk_list.html")))
    respx.get(url__regex=r"https://www\.motor-talk\.de/forum/.*-t\d+\.html.*").mock(
        return_value=httpx.Response(200, content=fixture("motortalk_thread.html")))
    out = sites.motor_talk(ctx(), {"source_id": "motor-talk"}, g60(slugs={"motor-talk": ["bmw-5er-g60-g61-g90-g99-b1172"]}),
                           {})
    assert len(out.records) >= 9  # 50 threads answered with the same recorded thread: deduplicated by post id
    r = out.records[0]
    assert r["record_id"].startswith("motor-talk-post-") and r["language"] == "de" and r["country"] == "DE"
    assert r["market"] == "Europe" and r["published_at"] is not None and r["author_hash"] and "Digital Key" in r[
        "text_original"]


@respx.mock
def test_pistonheads_keeps_threads_naming_the_line():
    from app.fetch.adapters import sites

    respx.get(url__regex=r".*forum\.asp.*").mock(return_value=httpx.Response(200, content=fixture("pistonheads_list.html")))
    respx.get(url__regex=r".*topic\.asp.*").mock(return_value=httpx.Response(200, content=fixture("pistonheads_topic.html")))
    threads = sites.parse_pistonheads_list(fixture("pistonheads_list.html").decode(), "https://www.pistonheads.com/gassing/")
    assert len(threads) >= 20 and threads[-1]["latest"] is not None
    posts = sites.parse_pistonheads_topic(fixture("pistonheads_topic.html").decode())
    assert len(posts) == 6 and posts[0]["published"].year == 2025 and "M2" in posts[0]["text"]
    subject = g60(slugs={"pistonheads": ["72"]}, terms=[{"term": "M2", "language": "en", "collision_safe": False,
                                                         "scope": "single_brand_source"}])
    out = sites.pistonheads(ctx(), {"source_id": "pistonheads"}, subject, {})
    assert all(r["search_term"] == "m2" and r["country"] == "GB" for r in out.records)


@respx.mock
def test_carcomplaints_complaints():
    import datetime as dt

    from app.fetch.adapters import sites

    respx.get("https://www.carcomplaints.com/Mercedes-Benz/E350/").mock(
        return_value=httpx.Response(200, content=fixture("carcomplaints_variant.html")))
    respx.get("https://www.carcomplaints.com/Mercedes-Benz/E350/2012/").mock(
        return_value=httpx.Response(200, content=fixture("carcomplaints_year.html")))
    respx.get(url__regex=r"https://www\.carcomplaints\.com/Mercedes-Benz/E350/2012/.*\.shtml").mock(
        return_value=httpx.Response(200, content=fixture("carcomplaints_problem.html")))
    respx.get(url__regex=r"https://www\.carcomplaints\.com/.*").mock(return_value=httpx.Response(404, text=""))
    subject = g60(kind="competitor", id="c-e-class", vehicle_role="competitor", slugs={"carcomplaints": ["Mercedes-Benz/E350"]})
    out = sites.carcomplaints(ctx(), {"source_id": "carcomplaints"}, subject, {"first_year-c-e-class": 2012})
    assert out.records and all(r["model_year"] == "2012" and r["category"] == "A" for r in out.records)
    assert out.records[0]["published_at"].date() == dt.date(2016, 9, 20)
    again = sites.carcomplaints(ctx(), {"source_id": "carcomplaints"}, subject, {**out.cursor, "first_year-c-e-class": 2012})
    assert again.records == []  # incremental: known problem pages are skipped


@respx.mock
def test_model_page_and_linked_tests():
    from app.fetch.adapters import sites

    respx.get("https://www.autobild.de/marken-modelle/bmw/5er/").mock(
        return_value=httpx.Response(200, content=fixture("autobild_model.html")))
    respx.get(url__regex=r"https://www\.autobild\.de/.*").mock(return_value=httpx.Response(200, text=ARTICLE))
    subject = g60(slugs={"autobild": ["bmw/5er"]}, terms=[{"term": "BMW 5er", "language": "de", "collision_safe": True,
                                                          "scope": "any"}])
    out = sites._model_pages(ctx(), {"source_id": "autobild"}, subject, {})
    articles = [r for r in out.records if r["category"] == "D" and r["content_type"] == "expert"]
    assert articles and all(r["language"] == "de" and r["published_at"] for r in articles)
    assert all("autobild.de" in r["url"] for r in articles)


@respx.mock
def test_newsroom_keeps_articles_naming_a_subject():
    from app.fetch.adapters import sites

    respx.get("https://www.audi.com/en/press-releases").mock(
        return_value=httpx.Response(200, content=fixture("press_audi.html")))
    respx.get(url__regex=r"https://www\.audi\.com/.*").mock(return_value=httpx.Response(200, text=ARTICLE.replace(
        "Der neue BMW 5er im Test: die Sitzbelüftung ist zu schwach.", "The Audi A6 allroad gets quattro ultra.")))
    a6 = g60(kind="competitor", id="c-a6", brand="Audi", model="A6", vehicle_role="competitor",
             terms=[{"term": "Audi A6", "language": "en", "collision_safe": True, "scope": "any"}])
    out = sites.fetch_newsroom(ctx(), {"source_id": "press-audi"}, [a6, g60()], {})
    assert out.records and all(r["search_term"] in ("audi a6", "audi-a6") for r in out.records)
    assert all(r["content_type"] == "press" and r["category"] == "J" for r in out.records)
    assert sites.fetch_newsroom(ctx(), {"source_id": "press-audi"}, [a6], out.cursor).records == []


def test_library_sources_report_missing_setup(monkeypatch):
    from app.config import get_settings
    from app.fetch.adapters import libraries
    from app.fetch.adapters.base import NotConfigured

    monkeypatch.setattr(get_settings(), "youtube_api_key", "")
    with pytest.raises(NotConfigured):
        libraries.youtube_transcripts(ctx(), {"source_id": "youtube-transcripts"}, g60(), {})


def test_predecessor_subject_is_the_older_car(conn):
    from app.fetch import predecessor

    for code, pred in (("G30", None), ("G60", "G30")):
        audit.insert(conn, "vehicle", {"code": code, "series": "5 Series", "is_current": code == "G60",
                                       "predecessor_code": pred}, actor="seed:test", key="code")
    s = predecessor.predecessor_subject(conn, "G60")
    assert (s.id, s.chassis_code, s.vehicle_role, s.wikipedia_page) == ("G60", "G30", "predecessor",
                                                                        "BMW 5 Series (G30)")


def test_cadence_and_market_scope():
    import datetime as dt

    now = dt.datetime(2026, 10, 11, 3, tzinfo=dt.timezone.utc)
    weekly = {"refresh_days": 7, "last_run_at": now - dt.timedelta(days=3)}
    assert not worker.is_due(weekly, now) and worker.is_due({**weekly, "last_run_at": now - dt.timedelta(days=7)}, now)
    assert worker.is_due({"refresh_days": 1, "last_run_at": None}, now)
    daily = {"refresh_days": 1, "last_run_at": now - dt.timedelta(hours=23)}  # cron a bit earlier than yesterday
    assert worker.is_due(daily, now)
    us_only = {"country": "US"}
    assert worker.in_market(us_only, g60(markets=["USA", "Europe"]))
    assert not worker.in_market(us_only, g60(markets=["China"]))
    assert worker.in_market({"country": "Global"}, g60(markets=["China"]))


def test_source_decisions_disable_reddit(conn):
    from app.seed import registry

    for sid, step in (("reddit", 2), ("motor-talk", 5), ("kba", 9)):
        audit.insert(conn, "source_registry", {"source_id": sid, "name": sid, "fetch_step": step, "enabled": True},
                     actor="seed:test", key="source_id")
    assert registry.apply_source_decisions(conn) == 2  # motor-talk already has the default (daily)
    rows = {r["source_id"]: r for r in conn.execute("select * from source_registry").fetchall()}
    assert rows["reddit"]["enabled"] is False and "developer" in rows["reddit"]["disabled_reason"]
    assert (rows["motor-talk"]["refresh_days"], rows["kba"]["refresh_days"]) == (1, 30)
    assert registry.apply_source_decisions(conn) == 0  # idempotent


def test_relevance_filter_keeps_no_text_without_a_signal(conn):
    """A fetched record the LLM finds nothing in keeps only its marker row."""
    from app import audit
    from app.fetch import worker
    from app.llm import client as llm_client

    audit.insert(conn, "source_registry", {"source_id": "bmwblog", "name": "bmwblog"}, actor="seed:test",
                 key="source_id", on_conflict_do_nothing=True)
    record = audit.insert(conn, "source_record", {
        "record_id": "bmwblog-offtopic", "source_id": "bmwblog", "category": "C", "content_type": "news",
        "content_hash": "x", "title": "Factory tour", "text_original": "The museum opens a new exhibition in May."},
        actor="fetch:bmwblog", key="record_id")
    llm_client.set_llm(llm_client.FakeLLM({"signal_extract": lambda v: {"signals": []}}))
    out = worker.run_agents(conn, [record], None)
    assert out["not_relevant"] == 1 and out["valid"] == 0
    row = conn.execute("select text_original, title, deleted_at from source_record where record_id = 'bmwblog-offtopic'"
                       ).fetchone()
    assert row["text_original"] is None and row["title"] is None and row["deleted_at"] is not None
    # Without an LLM nothing is judged, so nothing is purged.
    llm_client.set_llm(None)
    other = audit.insert(conn, "source_record", {
        "record_id": "bmwblog-unread", "source_id": "bmwblog", "category": "C", "content_type": "news",
        "content_hash": "y", "text_original": "The museum opens a new exhibition in May."},
        actor="fetch:bmwblog", key="record_id")
    assert worker.run_agents(conn, [other], None)["not_relevant"] == 0
