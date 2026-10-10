"""Phase 9: the API on the synthetic data set (dev auth, one transaction per request)."""

import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app import audit
from app.api import auth
from app.config import get_settings
from app.main import app
from app.scoring import score
from tests.test_cases import VENT, _run_cases, world  # noqa: F401  (fixture)

PM = str(uuid.uuid4())
ENGINEER = str(uuid.uuid4())
OTHER_PM = str(uuid.uuid4())


@pytest.fixture
def api(world, monkeypatch):
    conn = world["conn"]
    _run_cases(conn)
    score.score_config(conn, world["config"]["id"])
    for uid, name, role, vehicles in ((PM, "Anna", "pm", ["G60"]), (ENGINEER, "Eng", "engineer", []),
                                      (OTHER_PM, "Li", "pm", ["G70"])):
        audit.insert(conn, "app_user", {"id": uuid.UUID(uid), "name": name, "role": role, "vehicles": vehicles},
                     actor="seed:test")
    conn.commit()
    monkeypatch.setattr(get_settings(), "auth_mode", "dev")
    client = TestClient(app)
    case_id = conn.execute("select c.id from case_item c join case_vehicle cv on cv.case_id = c.id"
                           " where c.feature_id = %s and cv.vehicle = 'G60' and cv.state = 'open'"
                           " order by c.id limit 1", (world["features"][VENT],)).fetchone()["id"]
    return {"client": client, "world": world, "case_id": case_id, "profile": str(world["profiles"]["balanced"]),
            "config": str(world["config"]["id"])}


def h(user: str) -> dict:
    return {"X-User-Id": user}


def test_auth_required_and_unknown_user(api):
    c = api["client"]
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/me").status_code == 401
    assert c.get("/me", headers=h(str(uuid.uuid4()))).status_code == 403
    me = c.get("/me", headers=h(PM)).json()
    assert me["actor"] == f"pm:{PM}" and me["vehicles"] == ["G60"]


def test_board_page_and_view_logged(api):
    c, case_id = api["client"], api["case_id"]
    board = c.get("/cases", params={"profile_id": api["profile"]}, headers=h(PM)).json()
    assert board and board[0]["rank"] == 1 and any(r["id"] == case_id for r in board)
    assert all(r["car_area"] for r in board)
    seats = c.get("/cases", params={"profile_id": api["profile"], "car_area": "seats"}, headers=h(PM)).json()
    assert seats and all(r["car_area"] == "seats" for r in seats)
    assert c.get("/cases", params={"profile_id": api["profile"], "state": "bogus"}, headers=h(PM)).status_code == 422
    page = c.get(f"/cases/{case_id}", headers=h(PM)).json()
    assert page["id"] == case_id and page["vehicles"] and page["scores"]
    trail = c.get(f"/audit/case/{case_id}", headers=h(PM)).json()
    assert trail[0]["action"] == "view" and trail[0]["actor"] == f"pm:{PM}" and trail[0]["request_id"]


def test_stale_page_save_is_409(api):
    c, case_id = api["client"], api["case_id"]
    ok = c.put(f"/cases/{case_id}/page", json={"body_md": "new", "expected_version": 1}, headers=h(PM))
    assert ok.status_code == 200 and ok.json()["body_version"] == 2
    stale = c.put(f"/cases/{case_id}/page", json={"body_md": "other", "expected_version": 1}, headers=h(PM))
    assert stale.status_code == 409


def test_confirm_generates_requirement_and_permissions(api):
    c, case_id = api["client"], api["case_id"]
    url = f"/cases/{case_id}/vehicles/G60/transition"
    assert c.post(url, json={"action": "confirm"}, headers=h(ENGINEER)).status_code == 403
    assert c.post(url, json={"action": "confirm"}, headers=h(OTHER_PM)).status_code == 403  # not their line
    out = c.post(url, json={"action": "confirm", "rationale": "PM meeting"}, headers=h(PM)).json()
    assert out["state"] == "confirmed" and out["requirements"]
    assert c.post(url, json={"action": "confirm"}, headers=h(PM)).status_code == 409  # already confirmed
    rid = next(r for r in out["requirements"] if "-CN-" not in r)  # the USA profile shows USA only
    # Engineers edit and estimate cost; only the PM approves.
    assert c.post(f"/requirements/{rid}/cost", json={"cost_size": "L", "rationale": "new hardware"},
                  headers=h(ENGINEER)).json()["cost_source"] == "engineering"
    assert c.post(f"/requirements/{rid}/status", json={"status": "approved"}, headers=h(ENGINEER)).status_code == 403
    assert c.post(f"/requirements/{rid}/status", json={"status": "approved"}, headers=h(PM)).json()["status"] == \
        "approved"
    board = c.get("/requirements", params={"profile_id": api["profile"]}, headers=h(PM)).json()
    assert any(r["id"] == rid for r in board)
    export = c.get("/requirements/export", params={"profile_id": api["profile"]}, headers=h(PM)).json()
    assert [i["id"] for i in export["items"]] == [rid]
    csv = c.get("/requirements/export", params={"profile_id": api["profile"], "format": "csv"}, headers=h(PM))
    assert csv.text.startswith("rank,id,title")
    chain = c.get(f"/audit-chain/{rid}", headers=h(PM)).json()
    assert chain["signals"] and chain["events"]["requirement"] and any(
        d["action"] == "confirm" for d in chain["decisions"]) and any(d["action"] == "approve" for d in chain["decisions"])
    deleted = c.post(f"/requirements/{rid}/delete", json={"rationale": "duplicate"}, headers=h(ENGINEER)).json()
    assert deleted["status"] == "deleted"


def test_profiles_compare_and_source_filter(api):
    c, cfg = api["client"], api["config"]
    created = c.post(f"/configs/{cfg}/profiles", json={"name": "trend", "case_weights": {"trend": 1, "severity": 1}},
                     headers=h(PM))
    assert created.status_code == 201
    bad = c.post(f"/configs/{cfg}/profiles", json={"name": "x", "case_weights": {"vibes": 1}}, headers=h(PM))
    assert bad.status_code == 422
    compare = c.get(f"/configs/{cfg}/compare", params={"top_n": 3}, headers=h(PM)).json()
    assert set(compare["profiles"]) == {"balanced", "customers only", "trend"}
    patched = c.patch(f"/configs/{cfg}", json={"source_filter": {"exclude_sources": ["reddit"]},
                                                "rationale": "not the target audience"}, headers=h(PM))
    assert patched.status_code == 200
    board = c.get("/cases", params={"profile_id": api["profile"]}, headers=h(PM)).json()
    assert all("Source filter skips sources: reddit" in r["rationale"] for r in board if r["rationale"])


def test_chat_cites_signals(api):
    c, case_id = api["client"], api["case_id"]
    from app import db
    from app.rag import index

    with db.transaction() as conn:
        index.rebuild(conn)
    msg = c.post(f"/chat/case/{case_id}", json={"question": "seat ventilation too weak"}, headers=h(PM)).json()
    assert msg["role"] == "agent" and set(msg["cited_signals"]) <= set(msg["retrieved_signals"])
    assert len(c.get(f"/chat/case/{case_id}", headers=h(PM)).json()) == 2


def test_supabase_token_hs256(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_jwt_secret", "s" * 32)
    token = jwt.encode({"sub": PM, "aud": "authenticated"}, "s" * 32, algorithm="HS256")
    assert auth.verify_token(token) == PM
    with pytest.raises(Exception):
        auth.verify_token(jwt.encode({"sub": PM, "aud": "authenticated"}, "x" * 32, algorithm="HS256"))


def test_taxonomy_review_maps_to_existing_topic(api):
    c, world = api["client"], api["world"]
    conn = world["conn"]
    from app.signals import service
    from tests.test_cases import TOPICS

    audit.insert(conn, "source_record", {"record_id": "reddit-other", "source_id": "reddit", "content_hash": "o",
                                         "text_original": "The seats get hot and sticky in summer"},
                 actor="agent:feedback", key="record_id")
    out = service.ingest(conn, [{"record_id": "reddit-other", "kind": "complaint", "severity": "difficult_to_use",
                           "summary": "Seats hot in summer", "quote": "seats get hot and sticky", "topic_id": "other",
                           "aspect_group": "comfort", "vehicle": "G60", "customer_ref": "o"}], actor="agent:feedback")
    assert out["valid"] == 1, out
    conn.commit()
    queue = c.get("/taxonomy/review", headers=h(PM)).json()
    assert len(queue) == 1 and queue[0]["topic_id"] == "other"
    url = f"/taxonomy/review/{queue[0]['id']}"
    assert c.post(url, json={"action": "map", "topic_id": "invented_topic", "rationale": "x"},
                  headers=h(PM)).status_code == 422
    done = c.post(url, json={"action": "map", "topic_id": TOPICS[VENT], "rationale": "seat ventilation"},
                  headers=h(PM)).json()
    assert done["status"] == "mapped_to_existing"
    assert c.get("/taxonomy/review", headers=h(PM)).json() == []
    row = conn.execute("select classified_by, car_area from signal where record_id = 'reddit-other'").fetchone()
    assert row == {"classified_by": "pm", "car_area": ["seats"]}
