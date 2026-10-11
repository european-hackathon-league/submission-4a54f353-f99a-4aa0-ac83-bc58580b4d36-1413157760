"""AI proposals: the AI never edits, it proposes; a person accepts or rejects.

A proposal row records what was proposed and what the person decided. Accepting
applies the change through the normal audited write path of its object: the
caller (API route or UI adapter) does that first, then records the decision
here. The one kind applied here is a page diff of the RAG agent, which carries
its own stale-save check.
"""

from __future__ import annotations

import datetime as dt
import uuid

from app import audit

FIELDS = ("object_type", "object_id", "source", "kind", "summary", "changes", "question_id", "citations",
          "chat_message_id", "meeting_note_id")


def create(conn, data: dict, *, agent: str, requested_by: str | None = None, request_id=None) -> dict:
    """``agent`` is the proposing agent's name (note_extractor, drafter, monitor, ...)."""
    row = {k: data[k] for k in FIELDS if data.get(k) is not None}
    return audit.insert(conn, "proposal", {**row, "created_by": f"agent:{agent}"}, actor=f"agent:{agent}",
                        rationale=data.get("summary") if not requested_by else
                        f"{data.get('summary')} (asked by {requested_by})", request_id=request_id)


def from_chat_edit(conn, message: dict, *, request_id=None) -> dict:
    """The RAG agent proposed a page edit: the same proposal, decided the same way as every other."""
    diff = message["proposed_diff"]
    return create(conn, {
        "object_type": message["object_type"], "object_id": message["object_id"], "source": "chat", "kind": "body",
        "summary": diff.get("reason") or "Page edit proposed by the assistant",
        "changes": [{"field": "bodyMd", "before": None, "after": diff.get("body_md")}],
        "chat_message_id": message["id"]}, agent="rag", request_id=request_id)


def listing(conn, object_type: str | None = None, object_ids: list[str] | None = None,
            status: str | None = None) -> list[dict]:
    return conn.execute(
        "select * from proposal where (%(t)s::text is null or object_type = %(t)s)"
        " and (%(ids)s::text[] is null or object_id = any(%(ids)s::text[]))"
        " and (%(s)s::text is null or status = %(s)s) order by created_at",
        {"t": object_type, "ids": object_ids, "s": status}).fetchall()


def decide(conn, proposal_id: str, user, accept: bool, note: str | None = None, *, request_id=None) -> dict:
    from app.rag import agent

    p = audit.fetch_for_update(conn, "proposal", {"id": proposal_id})
    if p["status"] != "pending":
        raise ValueError("the proposal is already decided")
    if p["chat_message_id"]:
        # The page edit is applied with its stale-save check; decide_edit records the decision on this row too.
        agent.decide_edit(conn, str(p["chat_message_id"]), accept, actor=user.actor, request_id=request_id)
        return conn.execute("select * from proposal where id = %s", (proposal_id,)).fetchone()
    return audit.update(conn, "proposal", {"id": proposal_id}, {
        "status": "accepted" if accept else "rejected", "decided_by": uuid.UUID(user.id),
        "decided_at": dt.datetime.now(dt.timezone.utc), "decision_note": note}, actor=user.actor,
        rationale=note or p["summary"], action="accept" if accept else "reject", request_id=request_id)
