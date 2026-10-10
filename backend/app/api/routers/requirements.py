"""Requirements: board, page, status flow, re-target, cost, rank, tombstone delete,
handover export of the approved top 10 (logged)."""

from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import audit
from app.api.auth import User, current_user, require_editor, require_pm
from app.api.deps import conn, request_id
from app.rag import index
from app.requirements import service

router = APIRouter(prefix="/requirements", tags=["requirements"])
STATUSES = ("draft", "in_discussion", "approved", "rejected", "deleted")


def _vehicle(c, rid: str) -> str:
    row = c.execute("select vehicle from requirement where id = %s", (rid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{rid} not found")
    return row["vehicle"]


@router.get("")
def board(profile_id: str, status: list[str] = Query(default=["draft", "in_discussion", "approved"]),
          release_target: str | None = None, car_area: str | None = None, market: str | None = None,
          c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    """Requirements of the profile's line ranked for this profile (case score x cost multiplier)."""
    bad = set(status) - set(STATUSES)
    if bad:
        raise HTTPException(422, f"unknown statuses {sorted(bad)}")
    if not c.execute("select 1 from weight_profile where id = %s", (profile_id,)).fetchone():
        raise HTTPException(404, "weight profile not found")
    rows = service.board(c, profile_id, statuses=tuple(status))
    areas = {r["id"]: r for r in c.execute(
        "select r.id, f.car_area, f.name as topic from requirement r join case_item ci on ci.id = r.case_id"
        " join feature f on f.id = ci.feature_id where r.id = any(%s)", ([r["id"] for r in rows],)).fetchall()}
    out = []
    for r in rows:
        r.update(car_area=areas[r["id"]]["car_area"], topic=areas[r["id"]]["topic"])
        if release_target and r["release_target"] != release_target:
            continue
        if car_area and r["car_area"] != car_area:
            continue
        if market and r["market"] not in (None, market):
            continue
        out.append(r)
    return out


@router.get("/export")
def export(profile_id: str, format: Literal["json", "csv"] = "json", top_n: int = 10, c=Depends(conn),
           user: User = Depends(current_user)):
    """Handover to engineering: the approved top N with their arguments (customer
    complaints, wishes, competition, cost). The codebeamer / Jira format is a later
    feature; the export is logged as a decision and an audit event."""
    profile = c.execute("select p.id, c.vehicle, c.market, p.name from weight_profile p join config c"
                        " on c.id = p.config_id where p.id = %s", (profile_id,)).fetchone()
    if profile is None:
        raise HTTPException(404, "weight profile not found")
    rows = service.board(c, profile_id, statuses=("approved",))[:top_n]
    items = []
    for r in rows:
        req = c.execute("select * from requirement where id = %s", (r["id"],)).fetchone()
        fig = c.execute("select figures from case_item where id = %s", (r["case_id"],)).fetchone()["figures"] or {}
        vf = fig.get("by_vehicle", {}).get(profile["vehicle"], {})
        m = vf.get("markets", {}).get(profile["market"], {})
        items.append({
            "rank": r["rank"], "id": r["id"], "title": req["title"], "case_id": r["case_id"],
            "release_target": req["release_target"], "evidence_level": req["evidence_level"],
            "complaints": m.get("complaints", 0), "wishes": m.get("wishes", 0), "customers": m.get("customers", 0),
            "defects": m.get("defects", 0),
            "competition": f"{vf.get('competitor_evidence', {}).get('signals', 0)} competitor signals, "
                           f"{len(vf.get('competitor_offers', []))} competitor facts",
            "cost_size": req["cost_size"], "cost_source": req["cost_source"],
            "metric_target": req["metric_target"], "acceptance_criteria": req["acceptance_criteria"],
            "rank_score": r["rank_score"],
        })
        audit.insert(c, "decision", {"object_type": "requirement", "object_id": r["id"], "vehicle": req["vehicle"],
                                     "actor": user.actor, "action": "export",
                                     "rationale": f"export {format}, profile {profile['name']}"},
                     actor=user.actor, request_id=request_id())
    audit.log_access(c, action="export", object_type="weight_profile", object_id=str(profile_id), actor=user.actor,
                     request_id=request_id())
    if format == "json":
        return {"vehicle": profile["vehicle"], "market": profile["market"], "profile": profile["name"],
                "items": items}
    buf = io.StringIO()
    fields = ["rank", "id", "title", "case_id", "release_target", "evidence_level", "customers", "complaints",
              "wishes", "defects", "competition", "cost_size", "cost_source", "rank_score"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(items)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@router.get("/{rid}")
def get_requirement(rid: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    row = c.execute("select * from requirement where id = %s", (rid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{rid} not found")
    row["case"] = c.execute("select c.id, c.title, c.aspect_group, f.topic_id, f.name as topic, f.car_area"
                            " from case_item c join feature f on f.id = c.feature_id where c.id = %s",
                            (row["case_id"],)).fetchone()
    row["decisions"] = c.execute("select actor, action, rationale, created_at from decision where"
                                 " object_type = 'requirement' and object_id = %s order by created_at desc",
                                 (rid,)).fetchall()
    row["ranks"] = c.execute("select weight_profile_id, rank_score, cost_missing from requirement_rank"
                             " where requirement_id = %s", (rid,)).fetchall()
    audit.log_access(c, action="view", object_type="requirement", object_id=rid, actor=user.actor,
                     request_id=request_id())
    return row


@router.get("/{rid}/versions")
def versions(rid: str, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select version, body_md, author, created_at from page_version where"
                     " object_type = 'requirement' and object_id = %s order by version desc", (rid,)).fetchall()


class PageSave(BaseModel):
    body_md: str
    expected_version: int


@router.put("/{rid}/page")
def save_page(rid: str, body: PageSave, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_editor(user, _vehicle(c, rid))
    row = service.save_page(c, rid, body.body_md, body.expected_version, actor=user.actor, request_id=request_id())
    index.refresh_page(c, "requirement", rid)
    return {"id": row["id"], "body_version": row["body_version"]}


class Status(BaseModel):
    status: Literal["draft", "in_discussion", "approved", "rejected"]
    rationale: str | None = None


@router.post("/{rid}/status")
def set_status(rid: str, body: Status, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    vehicle = _vehicle(c, rid)
    if body.status == "approved":
        require_pm(user, vehicle)
    else:
        require_editor(user, vehicle)
    row = service.set_status(c, rid, body.status, actor=user.actor, rationale=body.rationale,
                             request_id=request_id())
    return {"id": row["id"], "status": row["status"]}


class Retarget(BaseModel):
    release_target: Literal["next_gen", "current_car", "ota"]
    rationale: str


@router.post("/{rid}/retarget")
def retarget(rid: str, body: Retarget, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_editor(user, _vehicle(c, rid))
    row = service.retarget(c, rid, body.release_target, actor=user.actor, rationale=body.rationale,
                           request_id=request_id())
    return {"id": row["id"], "release_target": row["release_target"]}


class Cost(BaseModel):
    cost_size: Literal["S", "M", "L"]
    rationale: str


@router.post("/{rid}/cost")
def set_cost(rid: str, body: Cost, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_editor(user, _vehicle(c, rid))
    row = service.set_cost(c, rid, body.cost_size, actor=user.actor, rationale=body.rationale,
                           request_id=request_id())
    return {"id": row["id"], "cost_size": row["cost_size"], "cost_source": row["cost_source"]}


class Rank(BaseModel):
    rank: int | None
    reason: str | None = None


@router.post("/{rid}/rank")
def set_rank(rid: str, body: Rank, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_pm(user, _vehicle(c, rid))
    row = service.set_manual_rank(c, rid, body.rank, actor=user.actor, reason=body.reason, request_id=request_id())
    return {"id": row["id"], "manual_rank": row["manual_rank"], "manual_rank_reason": row["manual_rank_reason"]}


class Delete(BaseModel):
    rationale: str


@router.post("/{rid}/delete")
def delete(rid: str, body: Delete, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Tombstone: title and body purged, row and history kept."""
    require_editor(user, _vehicle(c, rid))
    row = service.delete(c, rid, actor=user.actor, rationale=body.rationale, request_id=request_id())
    index.refresh_page(c, "requirement", rid)
    return {"id": row["id"], "status": row["status"], "deleted_at": row["deleted_at"]}
