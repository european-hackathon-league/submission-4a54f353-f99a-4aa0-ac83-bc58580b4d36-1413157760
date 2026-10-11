"""Reference data and configuration: vehicles, taxonomy, competitors, configs,
weight profiles (+ compare), brand profile, sources."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import User, current_user, require_pm
from app.api.deps import conn, request_id
from app.scoring import profiles, score

router = APIRouter(tags=["configuration"])


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role, "vehicles": list(user.vehicles),
            "actor": user.actor, "pm_role": user.pm_role, "region": user.region, "is_head": user.is_head}


@router.get("/vehicles")
def vehicles(c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select code, series, segment, generation, on_sale_since, predecessor_code, is_current,"
                     " markets_sold, variants from vehicle order by is_current desc, code").fetchall()


@router.get("/taxonomy")
def taxonomy_lists(c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """The fixed taxonomy: car areas, aspect groups, topics (closed lists)."""
    return {
        "car_areas": c.execute("select code, name, description from car_area order by code").fetchall(),
        "aspect_groups": c.execute("select code, description from aspect_group order by code").fetchall(),
        "topics": c.execute("select id, topic_id, name, car_area, hint, taxonomy_version from feature"
                            " order by car_area nulls last, name").fetchall(),
    }


@router.get("/competitors")
def competitors(vehicle: str | None = None, c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select id, bmw_vehicle, brand, model, generation, since, markets, status, origin, confirmed"
                     " from competitor where %s::text is null or bmw_vehicle = %s order by bmw_vehicle, brand, model",
                     (vehicle, vehicle)).fetchall()


class Confirm(BaseModel):
    confirmed: bool
    rationale: str


@router.post("/competitors/{competitor_id}/confirm")
def confirm_competitor(competitor_id: str, body: Confirm, c=Depends(conn), user: User = Depends(current_user)):
    row = c.execute("select bmw_vehicle from competitor where id = %s", (competitor_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "competitor not found")
    require_pm(user, row["bmw_vehicle"])
    return profiles.confirm_competitor(c, competitor_id, body.confirmed, actor=user.actor, rationale=body.rationale,
                                       request_id=request_id())


@router.get("/sources")
def sources(c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    return c.execute("select source_id, name, categories, country, level, access, check_status, fetch_step,"
                     " enabled, backfill_done_at from source_registry order by fetch_step nulls first, source_id"
                     ).fetchall()


@router.get("/configs")
def configs(c=Depends(conn), user: User = Depends(current_user)) -> list[dict]:
    rows = c.execute("select c.*, (select units from sales_volume s where s.vehicle = c.vehicle and"
                     " s.market = c.market and s.year = 2030) as sales_2030 from config c"
                     " order by c.vehicle, c.market").fetchall()
    for r in rows:
        r["profiles"] = c.execute("select id, name, is_default from weight_profile where config_id = %s"
                                  " order by is_default desc, name", (r["id"],)).fetchall()
    return rows


def _config(c, config_id) -> dict:
    row = c.execute("select * from config where id = %s", (config_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "config not found")
    return row


@router.get("/configs/{config_id}")
def config(config_id: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    row = _config(c, config_id)
    row["profiles"] = c.execute("select * from weight_profile where config_id = %s order by is_default desc, name",
                                (config_id,)).fetchall()
    row["competitors"] = c.execute("select id, brand, model, generation, confirmed from competitor"
                                   " where id = any(%s)", (row["competitor_ids"],)).fetchall()
    return row


class ConfigPatch(BaseModel):
    source_filter: dict[str, list[str]] | None = None
    thresholds: dict[str, Any] | None = None
    competitor_ids: list[str] | None = None
    active: bool | None = None
    rationale: str | None = None


@router.patch("/configs/{config_id}")
def patch_config(config_id: str, body: ConfigPatch, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_pm(user, _config(c, config_id)["vehicle"])
    return profiles.update_config(c, config_id, body.model_dump(exclude={"rationale"}), actor=user.actor,
                                  rationale=body.rationale, request_id=request_id())


class ProfileIn(BaseModel):
    description: str | None = None
    name: str
    case_weights: dict[str, float]
    brand_multipliers: dict[str, float] | None = None
    cost_multipliers: dict[str, float] | None = None
    rationale: str | None = None


@router.post("/configs/{config_id}/profiles", status_code=201)
def create_profile(config_id: str, body: ProfileIn, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_pm(user, _config(c, config_id)["vehicle"])
    return profiles.create_profile(c, config_id, body.name, body.case_weights, actor=user.actor,
                                   brand_multipliers=body.brand_multipliers, cost_multipliers=body.cost_multipliers,
                                   rationale=body.rationale, request_id=request_id(),
                                   description=body.description, owner_id=user.id)


class ProfilePatch(BaseModel):
    description: str | None = None
    name: str | None = None
    case_weights: dict[str, float] | None = None
    brand_multipliers: dict[str, float] | None = None
    cost_multipliers: dict[str, float] | None = None
    is_default: bool | None = None
    rationale: str | None = None


@router.patch("/profiles/{profile_id}")
def patch_profile(profile_id: str, body: ProfilePatch, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    row = c.execute("select c.vehicle from weight_profile p join config c on c.id = p.config_id where p.id = %s",
                    (profile_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "profile not found")
    require_pm(user, row["vehicle"])
    return profiles.update_profile(c, profile_id, body.model_dump(exclude={"rationale"}), actor=user.actor,
                                   rationale=body.rationale, request_id=request_id())


@router.get("/configs/{config_id}/compare")
def compare(config_id: str, top_n: int = 10, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    """Top N per weight profile and the cases that stay on top under every profile."""
    _config(c, config_id)
    result = score.compare_profiles(c, config_id, top_n)
    ids = sorted({i for top in result["profiles"].values() for i in top})
    result["titles"] = {r["id"]: r["title"] for r in c.execute(
        "select id, title from case_item where id = any(%s)", (ids,)).fetchall()}
    return result


@router.post("/configs/{config_id}/rescore")
def rescore(config_id: str, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_pm(user, _config(c, config_id)["vehicle"])
    return {"scores": score.score_config(c, config_id)}


@router.get("/brand-profile")
def brand_profile(c=Depends(conn), user: User = Depends(current_user)) -> dict:
    row = c.execute("select * from brand_profile order by version desc limit 1").fetchone()
    if row is None:
        raise HTTPException(404, "no brand profile")
    return row


class BrandProfileIn(BaseModel):
    body_md: str
    rationale: str
    pillars: list[dict] | None = None   # [{name, description}]
    do_not: list[str] | None = None


@router.post("/brand-profile", status_code=201)
def new_brand_profile(body: BrandProfileIn, c=Depends(conn), user: User = Depends(current_user)) -> dict:
    require_pm(user)
    if not user.is_head:
        raise HTTPException(403, "only the head of product line edits the brand profile")
    return profiles.new_brand_profile(c, body.body_md, actor=user.actor, rationale=body.rationale,
                                      pillars=body.pillars, do_not=body.do_not, request_id=request_id())
