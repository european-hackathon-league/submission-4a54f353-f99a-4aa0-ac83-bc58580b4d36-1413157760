"""Cases: board, overview map, page, lifecycle, ranking, tags, merge / split.

Confirming a case for a line generates its requirement(s) in the same request.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app import audit
from app.api.auth import User, current_user, require_pm
from app.api.deps import conn, request_id
from app.cases import cluster, lifecycle, pages, queries
from app.rag import index
from app.requirements import generator
from app.scoring import rank

router = APIRouter(prefix="/cases", tags=["cases"])


def _vehicles(c, case_id: str) -> list[str]:
    rows = c.execute("select vehicle from case_vehicle where case_id = %s", (case_id,)).fetchall()
    if not rows and not c.execute("select 1 from case_item where id = %s", (case_id,)).fetchone():
        raise HTTPException(404, f"{case_id} not found")
    return [r["vehicle"] for r in rows]


def _require_pm_of_case(c, user: User, case_id: str) -> None:
    vehicles = _vehicles(c, case_id)
    if user.role not in ("pm", "admin"):
        raise HTTPException(403, "only a PM can do this")
    if vehicles and not any(user.owns(v) for v in vehicles):
        raise HTTPException(403, f"{case_id} applies to none of your lines")


@router.get("")
def board(profile_id: str, state: list[str] = Query(default=["open", "confirmed"]), car_area: str | None = None,
          release_fit: str | None = None, market: str | None = None, source_type: str | None = None,
          source_id: str | None = None, aspect_group: str | None = None, conflict_only: bool = False,
          q: str | None = None, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    """Ranked case board of one weight profile (= one vehicle + market). Filters: state,
    car area, release fit, market (customers there), source type (content type), source."""
    bad = set(state) - set(queries.STATES)
    if bad:
        raise HTTPException(422, f"unknown states {sorted(bad)}")
    if not c.execute("select 1 from weight_profile where id = %s", (profile_id,)).fetchone():
        raise HTTPException(404, "weight profile not found")
    return queries.board(c, profile_id, states=tuple(state), car_area=car_area, release_fit=release_fit,
                         market=market, source_type=source_type, source_id=source_id, aspect_group=aspect_group,
                         conflict_only=conflict_only, q=q)


@router.get("/overview")
def overview(vehicle: str, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    """Overview map of a line: cases (observed included) per car area and state."""
    return queries.overview(c, vehicle)


@router.get("/{case_id}")
def get_case(case_id: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    page = queries.page(c, case_id)
    if page is None:
        raise HTTPException(404, f"{case_id} not found")
    audit.log_access(c, action="view", object_type="case", object_id=case_id, actor=user.actor,
                     request_id=request_id())
    return page


@router.get("/{case_id}/versions")
def versions(case_id: str, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select version, body_md, author, created_at from page_version where object_type = 'case'"
                     " and object_id = %s order by version desc", (case_id,)).fetchall()


class PageSave(BaseModel):
    body_md: str
    expected_version: int
    title: str | None = None


@router.put("/{case_id}/page")
def save_page(case_id: str, body: PageSave, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Save the page; 409 when someone saved first (stale version)."""
    _require_pm_of_case(c, user, case_id)
    row = pages.save_case_page(c, case_id, body.body_md, body.expected_version, actor=user.actor, title=body.title,
                               request_id=request_id())
    index.refresh_page(c, "case", case_id)
    return {"id": row["id"], "title": row["title"], "body_version": row["body_version"]}


class Transition(BaseModel):
    action: Literal["open", "confirm", "reject", "reopen"]
    rationale: str | None = None


@router.post("/{case_id}/vehicles/{vehicle}/transition")
def transition(case_id: str, vehicle: str, body: Transition, c=Depends(conn),
               user: User = Depends(current_user)) -> dict:
    """PM lifecycle action for one line. Confirm generates the requirement(s)."""
    require_pm(user, vehicle)
    row = lifecycle.transition(c, case_id, vehicle, body.action, actor=user.actor, rationale=body.rationale,
                               request_id=request_id())
    out = {"case_id": case_id, "vehicle": vehicle, "state": row["state"], "requirements": []}
    if body.action == "confirm":
        created = generator.generate(c, case_id, vehicle, request_id=request_id())
        out["requirements"] = [r["id"] for r in created]
        for r in created:
            index.refresh_page(c, "requirement", r["id"])
    return out


class Rank(BaseModel):
    rank: int | None
    reason: str | None = None


@router.post("/{case_id}/vehicles/{vehicle}/rank")
def set_rank(case_id: str, vehicle: str, body: Rank, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Drag on the board: a manual rank with a reason (null clears it)."""
    require_pm(user, vehicle)
    row = rank.set_manual_rank(c, case_id, vehicle, body.rank, actor=user.actor, reason=body.reason,
                               request_id=request_id())
    return {k: row[k] for k in ("case_id", "vehicle", "manual_rank", "manual_rank_reason")}


class Tag(BaseModel):
    field: Literal["brand_fit", "segment", "release_fit"]
    value: str
    rationale: str
    vehicle: str | None = None


TAG_VALUES = {"brand_fit": {"fits", "neutral", "off_brand"}, "segment": {"base", "premium", "luxury"},
              "release_fit": {"current_car", "next_gen", "ota"}}


@router.post("/{case_id}/tags")
def set_tag(case_id: str, body: Tag, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """PM override of an AI tag (brand fit, segment, or release fit per line)."""
    if body.value not in TAG_VALUES[body.field]:
        raise HTTPException(422, f"{body.field} is one of {sorted(TAG_VALUES[body.field])}")
    if body.vehicle:
        require_pm(user, body.vehicle)
    else:
        _require_pm_of_case(c, user, case_id)
    row = pages.set_tag(c, case_id, body.field, body.value, actor=user.actor, rationale=body.rationale,
                        vehicle=body.vehicle, request_id=request_id())
    row.pop("embedding", None)
    return row


class Merge(BaseModel):
    target_id: str
    rationale: str


@router.post("/{case_id}/merge")
def merge(case_id: str, body: Merge, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Move every signal of this case into the target; this case stays as rejected, pointing to it."""
    _require_pm_of_case(c, user, case_id)
    _vehicles(c, body.target_id)
    a = c.execute("select feature_id from case_item where id = %s", (case_id,)).fetchone()
    b = c.execute("select feature_id from case_item where id = %s", (body.target_id,)).fetchone()
    if a["feature_id"] != b["feature_id"]:
        raise HTTPException(422, "cases of different topics cannot be merged (grouping never crosses a topic)")
    cluster.merge(c, case_id, body.target_id, actor=user.actor, rationale=body.rationale)
    audit.insert(c, "decision", {"object_type": "case", "object_id": case_id, "actor": user.actor,
                                 "action": "merge", "rationale": f"into {body.target_id}: {body.rationale}"},
                 actor=user.actor, request_id=request_id())
    _refresh(c, body.target_id)
    return {"merged": case_id, "into": body.target_id}


class Split(BaseModel):
    signal_ids: list[str]
    rationale: str


@router.post("/{case_id}/split", status_code=201)
def split(case_id: str, body: Split, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Move the selected signals into a new case of the same topic and aspect group."""
    _require_pm_of_case(c, user, case_id)
    new = cluster.split(c, case_id, body.signal_ids, actor=user.actor, rationale=body.rationale)
    audit.insert(c, "decision", {"object_type": "case", "object_id": case_id, "actor": user.actor,
                                 "action": "split", "rationale": f"{new['id']}: {body.rationale}"},
                 actor=user.actor, request_id=request_id())
    _refresh(c, case_id)
    _refresh(c, new["id"])
    return {"from": case_id, "new_case": new["id"], "title": new["title"]}


def _refresh(c, case_id: str) -> None:
    """Figures, tags and open rules after a membership change; then rescore the lines' configs."""
    from app.cases import pipeline
    from app.scoring import score

    pipeline.update_case(c, case_id)
    for cfg in c.execute("select distinct cfg.id from config cfg join case_vehicle cv on cv.vehicle = cfg.vehicle"
                         " where cv.case_id = %s and cfg.active", (case_id,)).fetchall():
        score.score_config(c, cfg["id"], case_ids=[case_id])


@router.post("/{case_id}/vehicles/{vehicle}/requirements", status_code=201)
def generate_requirements(case_id: str, vehicle: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Generate the requirements of a confirmed case that has none yet for this line
    (existing IDs, deleted ones included, are never regenerated)."""
    require_pm(user, vehicle)
    created = generator.generate(c, case_id, vehicle, request_id=request_id())
    for r in created:
        index.refresh_page(c, "requirement", r["id"])
    return {"requirements": [r["id"] for r in created]}


class Research(BaseModel):
    rationale: str


@router.post("/{case_id}/vehicles/{vehicle}/predecessor-research", status_code=202)
def predecessor_research(case_id: str, vehicle: str, body: Research, background: BackgroundTasks, c=Depends(conn),
                         user: User = Depends(current_user)) -> dict:
    """PM flags "removed vs predecessor": the case is tagged (score factor) now, and the
    predecessor's pages are fetched in the background (agent_run mode on_demand)."""
    from app.fetch import predecessor

    require_pm(user, vehicle)
    _vehicles(c, case_id)
    out = predecessor.flag(c, case_id, vehicle, actor=user.actor, rationale=body.rationale, request_id=request_id())
    if out["predecessor"]:
        background.add_task(predecessor.research, case_id, vehicle, actor=user.actor)
    return {**out, "research": "scheduled" if out["predecessor"] else "no predecessor known"}
