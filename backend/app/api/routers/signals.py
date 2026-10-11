"""Signals, their source records, the quarantine and the taxonomy review queue."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import audit
from app.api.auth import User, current_user, require_pm
from app.api.deps import conn, request_id
from app.cases import queries
from app.signals import service

router = APIRouter(tags=["signals"])


@router.get("/signals")
def list_signals(case_id: str | None = None, vehicle: str | None = None, market: str | None = None,
                 kind: str | None = None, evidence_role: str | None = None, source_id: str | None = None,
                 content_type: str | None = None, car_area: str | None = None, topic_id: str | None = None,
                 unassigned: bool = False, q: str | None = None,
                 limit: int = 50, offset: int = 0, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Valid signals. ``unassigned`` = not in a case yet (loose signals), ``q`` = text in summary or quote."""
    return queries.signals(c, case_id=case_id, vehicle=vehicle, market=market, kind=kind, evidence_role=evidence_role,
                           source_id=source_id, content_type=content_type, car_area=car_area, topic_id=topic_id,
                           unassigned=unassigned, q=q, limit=min(limit, 2000), offset=offset)


@router.get("/signals/flow")
def signal_flow(vehicle: str, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    """Aggregated evidence flow of a line for the overview diagram: signals counted
    per source, content type, car area and case (never the 13,000 rows themselves)."""
    return c.execute(
        "select r.source_id, r.content_type, s.evidence_role, f.car_area, s.case_id, count(*) as signals"
        " from signal s join source_record r using (record_id) left join feature f on f.id = s.feature_id"
        " where s.validation = 'valid' and (s.vehicle = %s or s.vehicle is null)"
        " group by 1, 2, 3, 4, 5 order by signals desc", (vehicle,)).fetchall()


@router.get("/signals/{signal_id}")
def get_signal(signal_id: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """One signal with its full source record: the quote can be checked in place."""
    row = c.execute("select s.*, f.topic_id, f.name as topic from signal s left join feature f"
                    " on f.id = s.feature_id where s.id = %s", (signal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "signal not found")
    row.pop("embedding", None)
    row["source_record"] = c.execute("select * from source_record where record_id = %s",
                                     (row["record_id"],)).fetchone()
    if row["data_record_id"]:
        row["data_record"] = c.execute("select * from data_record where record_id = %s",
                                       (row["data_record_id"],)).fetchone()
    audit.log_access(c, action="view", object_type="signal", object_id=signal_id, actor=user.actor,
                     request_id=request_id())
    return row


@router.get("/quarantine")
def quarantine(limit: int = 100, offset: int = 0, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    return {
        "summary": c.execute("select * from signal_quarantine_summary order by signals desc").fetchall(),
        "total": c.execute("select count(*) n from signal_quarantine").fetchone()["n"],
        "items": c.execute("select * from signal_quarantine order by created_at desc limit %s offset %s",
                           (min(limit, 500), offset)).fetchall(),
    }


class Reason(BaseModel):
    rationale: str


@router.post("/quarantine/{signal_id}/delete")
def delete_quarantined(signal_id: str, body: Reason, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """PM deletes a quarantined signal: content purged, tombstone kept."""
    require_pm(user)
    row = service.delete_quarantined(c, signal_id, actor=user.actor, rationale=body.rationale,
                                     request_id=request_id())
    return {"id": str(row["id"]), "validation": row["validation"], "deleted_at": row["deleted_at"]}


class Release(BaseModel):
    changes: dict = {}
    rationale: str


@router.post("/quarantine/{signal_id}/release")
def release(signal_id: str, body: Release, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Correct a quarantined signal (e.g. kind, topic_id, aspect_group) and re-run the gate."""
    require_pm(user)
    row = service.release(c, signal_id, body.changes, actor=user.actor, rationale=body.rationale,
                          request_id=request_id())
    row.pop("embedding", None)
    return row


@router.get("/taxonomy/review")
def taxonomy_review(c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select * from taxonomy_review order by created_at desc limit 500").fetchall()


class Resolve(BaseModel):
    action: Literal["map", "dismiss"]
    topic_id: str | None = None
    aspect_group: str | None = None
    rationale: str


@router.post("/taxonomy/review/{proposal_id}")
def resolve(proposal_id: str, body: Resolve, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Map the signal to an existing topic or dismiss it; topics are never created here."""
    require_pm(user)
    return service.resolve_proposal(c, proposal_id, body.action, actor=user.actor, rationale=body.rationale,
                                    topic_id=body.topic_id, aspect_group=body.aspect_group, request_id=request_id())
