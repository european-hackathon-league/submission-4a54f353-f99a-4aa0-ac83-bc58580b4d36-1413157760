"""Phase 6: requirements on the synthetic data set."""

import pytest

from app import audit
from app.cases import lifecycle, pages
from app.requirements import generator, service
from app.scoring import score
from tests.test_cases import VENT, _run_cases, world  # noqa: F401  (fixture)


def _confirmed_vent(world):
    conn = world["conn"]
    _run_cases(conn)
    score.score_config(conn, world["config"]["id"])
    case_id = conn.execute("select id from case_item where feature_id = %s order by id limit 1",
                           (world["features"][VENT],)).fetchone()["id"]
    lifecycle.transition(conn, case_id, "G60", "confirm", actor="pm:anna", rationale="confirmed after PM meeting")
    return conn, case_id


def test_requirements_only_for_confirmed_cases(world):
    conn = world["conn"]
    _run_cases(conn)
    case_id = conn.execute("select id from case_item order by id limit 1").fetchone()["id"]
    vehicle = conn.execute("select vehicle from case_vehicle where case_id = %s limit 1", (case_id,)).fetchone()
    with pytest.raises(ValueError):
        generator.generate(conn, case_id, vehicle["vehicle"])


def test_generate_splits_per_market_on_conflict(world):
    conn, case_id = _confirmed_vent(world)
    created = generator.generate(conn, case_id, "G60")
    number = case_id.split("-")[1]
    assert sorted(r["id"] for r in created) == [f"REQ-G60-CN-{number}", f"REQ-G60-USA-{number}"]
    req = created[0]
    assert req["status"] == "draft" and req["evidence_level"] == "web"
    assert req["release_target"] == "next_gen" and req["cost_size"] in ("S", "M", "L")
    assert req["cost_source"] == "ai_estimate" and req["assumptions"]
    usa = next(r for r in created if r["market"] == "USA")
    assert usa["rank_score"] is not None  # default-profile cache; China has no config in this world
    assert req["market"] == "China" and req["rank_score"] is None
    assert generator.generate(conn, case_id, "G60") == []  # idempotent


def test_requirement_actions_are_audited(world):
    conn, case_id = _confirmed_vent(world)
    req = generator.generate(conn, case_id, "G60")[0]
    rid = req["id"]
    service.save_page(conn, rid, "edited body", 1, actor="pm:anna")
    with pytest.raises(audit.StaleVersion):
        service.save_page(conn, rid, "stale body", 1, actor="engineer:ben")
    service.set_cost(conn, rid, "L", actor="engineer:ben", rationale="needs new seat fans")
    with pytest.raises(service.InvalidAction):
        service.set_status(conn, rid, "approved", actor="engineer:ben", rationale="looks fine")
    service.set_status(conn, rid, "approved", actor="pm:anna", rationale="top 10")
    service.delete(conn, rid, actor="pm:anna", rationale="merged into another requirement")
    row = conn.execute("select status, body_md, cost_source, deleted_at from requirement where id = %s",
                       (rid,)).fetchone()
    assert row["status"] == "deleted" and row["body_md"] is None and row["cost_source"] == "engineering"
    actions = [r["action"] for r in conn.execute("select action from decision where object_id = %s order by created_at",
                                                 (rid,)).fetchall()]
    assert actions == ["edit", "edit", "approve", "delete"]


def test_requirement_board_ranks_per_profile(world):
    conn, case_id = _confirmed_vent(world)
    generator.generate(conn, case_id, "G60")
    board = service.board(conn, world["profiles"]["balanced"])
    assert [r["market"] for r in board] == ["USA"]  # the China requirement belongs to another config
    assert board[0]["rank_score"] == pytest.approx(board[0]["case_score"] * {"S": 1.0, "M": 0.85, "L": 0.7}[
        board[0]["cost_size"]], abs=1e-4)


def test_pm_tag_override_is_kept(world):
    conn, case_id = _confirmed_vent(world)
    pages.set_tag(conn, case_id, "release_fit", "ota", actor="pm:anna", rationale="software fan curve", vehicle="G60")
    from app.cases import pipeline

    pipeline.update_case(conn, case_id)
    cv = conn.execute("select release_fit, release_fit_source from case_vehicle where case_id = %s and vehicle = 'G60'",
                      (case_id,)).fetchone()
    assert cv == {"release_fit": "ota", "release_fit_source": "pm"}
