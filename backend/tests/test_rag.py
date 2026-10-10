"""Phase 7: RAG sidebar agent (hybrid search, citation check, proposed edits)."""

from app import audit
from app.rag import agent, index
from tests.test_cases import world  # noqa: F401  (fixture)
from tests.test_requirements import _confirmed_vent


def test_rag_answer_cites_only_retrieved_signals(world):
    conn, case_id = _confirmed_vent(world)
    index.rebuild(conn)
    msg = agent.ask(conn, "case", case_id, "seat ventilation too weak in hot weather", actor="pm:anna")
    assert msg["cited_signals"] and set(msg["cited_signals"]) <= set(msg["retrieved_signals"])
    assert "[signal:" in msg["content"]


def test_citation_check_removes_unknown_ids(conn):
    fake = "00000000-0000-0000-0000-000000000000"
    answer, cited = agent.check_citations(conn, f"Customers complain [signal:{fake}].", set())
    assert answer == agent.NO_EVIDENCE and cited == []


def test_proposed_edit_needs_pm_accept(world):
    conn, case_id = _confirmed_vent(world)
    turn = agent.Turn("case", case_id)
    agent.tool_propose_edit(conn, turn, "## Problem\n\nNew text", "clearer")
    msg = audit.insert(conn, "chat_message", {"object_type": "case", "object_id": case_id, "role": "agent",
                                              "author": "agent:rag", "content": "proposal",
                                              "proposed_diff": turn.proposal, "diff_status": "proposed"},
                       actor="agent:rag")
    before = conn.execute("select body_md from case_item where id = %s", (case_id,)).fetchone()["body_md"]
    assert before != "## Problem\n\nNew text"
    agent.decide_edit(conn, msg["id"], True, actor="pm:anna")
    after = conn.execute("select body_md, body_version from case_item where id = %s", (case_id,)).fetchone()
    assert after == {"body_md": "## Problem\n\nNew text", "body_version": 2}


