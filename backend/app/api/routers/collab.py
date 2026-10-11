"""RAG sidebar chat, audit trail, notifications, agent runs, RAG index rebuild."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import audit
from app.api.auth import User, current_user, require_editor, require_pm
from app.api.deps import conn, request_id
from app.rag import agent, index

router = APIRouter(tags=["collaboration"])
ObjectType = Literal["case", "requirement"]


def _exists(c, object_type: str, object_id: str) -> str | None:
    """The object's vehicle (requirement) or None (case); 404 when it does not exist."""
    table = "case_item" if object_type == "case" else "requirement"
    row = c.execute(f"select {'null' if object_type == 'case' else 'vehicle'} as vehicle from {table}"
                    " where id = %s", (object_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{object_id} not found")
    return row["vehicle"]


@router.get("/chat/{object_type}/{object_id}")
def history(object_type: ObjectType, object_id: str, c=Depends(conn), user: User = Depends(current_user)):
    _exists(c, object_type, object_id)
    return c.execute("select * from chat_message where object_type = %s and object_id = %s order by created_at",
                     (object_type, object_id)).fetchall()


class Question(BaseModel):
    question: str


@router.post("/chat/{object_type}/{object_id}")
def ask(object_type: ObjectType, object_id: str, body: Question, c=Depends(conn),
        user: User = Depends(current_user)) -> dict:
    """Ask the sidebar agent. Every claim cites a signal it retrieved; otherwise
    "No evidence found". Edits come back as a proposed diff the PM accepts or rejects."""
    _exists(c, object_type, object_id)
    return agent.ask(c, object_type, object_id, body.question, actor=user.actor, request_id=request_id())


class EditDecision(BaseModel):
    accept: bool


@router.post("/chat/messages/{message_id}/decision")
def decide(message_id: str, body: EditDecision, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    msg = c.execute("select object_type, object_id from chat_message where id = %s", (message_id,)).fetchone()
    if msg is None:
        raise HTTPException(404, "message not found")
    vehicle = _exists(c, msg["object_type"], msg["object_id"])
    if msg["object_type"] == "case":
        require_pm(user)
    else:
        require_editor(user, vehicle)
    row = agent.decide_edit(c, message_id, body.accept, actor=user.actor, request_id=request_id())
    if body.accept:
        index.refresh_page(c, msg["object_type"], msg["object_id"])
    return {"id": str(row["id"]), "diff_status": row["diff_status"]}


@router.get("/audit/{object_type}/{object_id}")
def audit_trail(object_type: str, object_id: str, limit: int = 200, c=Depends(conn),
                user: User = Depends(current_user)) -> list[dict]:
    """Audit events of one object (for a case also its per-line rows ``case_vehicle``)."""
    types = [object_type] + (["case_vehicle"] if object_type == "case" else [])
    return c.execute(
        "select seq, at, actor, actor_type, action, object_type, object_id, before, after, rationale, run_id,"
        " request_id from audit_event where object_type = any(%s) and (object_id = %s or object_id like %s)"
        " order by seq desc limit %s", (types, object_id, f"{object_id}/%", min(limit, 1000))).fetchall()


@router.get("/audit-chain/{requirement_id}")
def chain(requirement_id: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Audit chain of one requirement: source records -> signals -> case -> scores ->
    PM decisions -> requirement -> approval."""
    from app.reports import audit_chain

    return audit_chain.collect(c, requirement_id)


@router.get("/notifications")
def notifications(vehicle: str | None = None, unread: bool = False, limit: int = 50, c=Depends(conn),
                  user: User = Depends(current_user)) -> list[dict]:
    """My inbox items (user_id = me) and the broadcasts of my lines (user_id null)."""
    vehicles = [vehicle] if vehicle else list(user.vehicles)
    return c.execute(
        "select id, type, object_type, object_id, vehicle, market, message, created_at, user_id, title, link,"
        " actor, %s::uuid = any(read_by) as read from notification"
        " where (user_id = %s or (user_id is null and"
        " (cardinality(%s::text[]) = 0 or vehicle is null or vehicle = any(%s::text[]))))"
        " and (not %s or not (%s::uuid = any(read_by)))"
        " order by created_at desc limit %s",
        (user.id, user.id, vehicles, vehicles, unread, user.id, min(limit, 500))).fetchall()


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    row = c.execute("select read_by from notification where id = %s", (notification_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "notification not found")
    if uuid.UUID(user.id) in row["read_by"]:
        return {"id": notification_id, "updated": False}
    audit.update(c, "notification", {"id": notification_id}, {"read_by": [*row["read_by"], uuid.UUID(user.id)]},
                 actor=user.actor, rationale="notification read", request_id=request_id())
    return {"id": notification_id, "updated": True}


@router.get("/runs")
def runs(limit: int = 50, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select id, agent, mode, status, started_at, ended_at, records_fetched, signals_valid,"
                     " signals_quarantined, llm_tokens, llm_cost_usd, error, params from agent_run"
                     " order by started_at desc limit %s", (min(limit, 500),)).fetchall()


@router.post("/admin/rag/rebuild")
def rebuild(c=Depends(conn), user: User = Depends(current_user)) -> dict:
    if user.role != "admin":
        raise HTTPException(403, "admin only")
    return index.rebuild(c, actor=user.actor)
