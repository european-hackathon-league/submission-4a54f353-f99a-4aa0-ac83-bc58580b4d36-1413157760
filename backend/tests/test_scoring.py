"""Phase 5: case scoring, profile compare, manual rank, requirement rank."""

import pytest

from app.scoring import rank, score
from tests.test_cases import _run_cases, world  # noqa: F401  (fixture)


def test_scoring_breakdown_profiles_and_manual_rank(world):
    conn = world["conn"]
    _run_cases(conn)
    n = score.score_config(conn, world["config"]["id"])
    assert n > 0
    board = rank.case_board(conn, world["profiles"]["balanced"])
    top = board[0]
    assert top["score"] > 0 and "distinct customers" in top["rationale"]
    stored = conn.execute("select factors from case_score where case_id = %s limit 1", (top["id"],)).fetchone()
    assert set(stored["factors"]) == set(score.FACTORS)
    assert abs(sum(f["contribution"] for f in stored["factors"].values())
               - float(conn.execute("select score / brand_multiplier s from case_score where case_id = %s limit 1",
                                    (top["id"],)).fetchone()["s"])) < 0.01
    last = board[-1]
    rank.set_manual_rank(conn, last["id"], last["vehicle"], 1, actor="pm:anna", reason="CEO priority")
    board2 = rank.case_board(conn, world["profiles"]["balanced"])
    assert board2[0]["id"] == last["id"] and board2[0]["manual_rank_reason"] == "CEO priority"
    with pytest.raises(ValueError):
        rank.set_manual_rank(conn, last["id"], last["vehicle"], 2, actor="pm:anna", reason=None)
    compare = score.compare_profiles(conn, world["config"]["id"], top_n=1)
    assert set(compare["profiles"]) == {"balanced", "customers only"}


def test_weights_must_have_positive_sum():
    with pytest.raises(ValueError):
        score.normalize_weights({f: 0 for f in score.FACTORS})
    assert abs(sum(score.normalize_weights({"severity": 2, "trend": 2}).values()) - 1) < 1e-9


def test_requirement_rank_uses_cost_multiplier():
    assert rank.requirement_rank(0.8, "L", {"S": 1.0, "M": 0.85, "L": 0.7}) == 0.56
    assert rank.requirement_rank(0.8, None, None) == 0.8  # cost missing counts as 1.0, like the SQL view
