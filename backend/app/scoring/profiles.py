"""PM configuration: weight profiles, config settings (source filter, thresholds,
competitor set), brand profile versions, competitor confirmation. Every change is
audited; a change that affects scores triggers a rescore of the config."""

from __future__ import annotations

import uuid

from app import audit
from app.scoring.score import FACTORS, score_config

CONFIG_FIELDS = {"source_filter", "thresholds", "competitor_ids", "active"}
FILTER_KEYS = {"exclude_sources", "exclude_categories", "exclude_content_types"}


def _check_weights(weights: dict) -> dict:
    unknown = set(weights) - set(FACTORS)
    if unknown:
        raise ValueError(f"unknown factors {sorted(unknown)}; allowed: {list(FACTORS)}")
    clean = {f: float(weights.get(f, 0)) for f in FACTORS}
    if any(v < 0 for v in clean.values()) or sum(clean.values()) <= 0:
        raise ValueError("weights must be >= 0 with a positive sum")
    return clean


def create_profile(conn, config_id, name: str, case_weights: dict, *, actor: str, brand_multipliers=None,
                   cost_multipliers=None, rationale: str | None = None, request_id=None) -> dict:
    row = {"config_id": config_id, "name": name, "case_weights": _check_weights(case_weights),
           "created_by": actor, "is_default": False}
    if brand_multipliers:
        row["brand_multipliers"] = brand_multipliers
    if cost_multipliers:
        row["cost_multipliers"] = cost_multipliers
    profile = audit.insert(conn, "weight_profile", row, actor=actor, rationale=rationale or "new weight profile",
                           request_id=request_id)
    score_config(conn, config_id, profile_ids=[profile["id"]])
    return profile


def update_profile(conn, profile_id, changes: dict, *, actor: str, rationale: str | None = None,
                   request_id=None) -> dict:
    changes = {k: v for k, v in changes.items() if v is not None}
    if "case_weights" in changes:
        changes["case_weights"] = _check_weights(changes["case_weights"])
    if changes.get("is_default"):
        current = audit.fetch_for_update(conn, "weight_profile", {"id": profile_id})
        for other in conn.execute("select id from weight_profile where config_id = %s and is_default and id <> %s",
                                  (current["config_id"], profile_id)).fetchall():
            audit.update(conn, "weight_profile", {"id": other["id"]}, {"is_default": False}, actor=actor,
                         rationale="another profile became the default", request_id=request_id)
    row = audit.update(conn, "weight_profile", {"id": profile_id}, changes, actor=actor,
                       rationale=rationale or "weight profile edited", request_id=request_id)
    if {"case_weights", "brand_multipliers"} & set(changes):
        score_config(conn, row["config_id"], profile_ids=[profile_id])
    return row


def update_config(conn, config_id, changes: dict, *, actor: str, rationale: str | None = None,
                  request_id=None) -> dict:
    changes = {k: v for k, v in changes.items() if v is not None}
    unknown = set(changes) - CONFIG_FIELDS
    if unknown:
        raise ValueError(f"config fields {sorted(unknown)} cannot be changed here")
    if "source_filter" in changes:
        bad = set(changes["source_filter"]) - FILTER_KEYS
        if bad:
            raise ValueError(f"unknown source filter keys {sorted(bad)}; allowed: {sorted(FILTER_KEYS)}")
    if "competitor_ids" in changes:
        changes["competitor_ids"] = [uuid.UUID(str(i)) for i in changes["competitor_ids"]]
    if "thresholds" in changes:
        current = audit.fetch_for_update(conn, "config", {"id": config_id})
        changes["thresholds"] = {**(current["thresholds"] or {}), **changes["thresholds"]}
    row = audit.update(conn, "config", {"id": config_id}, changes, actor=actor,
                       rationale=rationale or "config edited", request_id=request_id)
    if {"source_filter", "thresholds"} & set(changes):
        score_config(conn, config_id)
    return row


def new_brand_profile(conn, body_md: str, *, actor: str, rationale: str, request_id=None) -> dict:
    """A new version; configs on the previous latest version move to it."""
    latest = conn.execute("select * from brand_profile order by version desc limit 1").fetchone()
    version = (latest["version"] + 1) if latest else 1
    row = audit.insert(conn, "brand_profile", {"version": version, "body_md": body_md, "created_by": actor},
                       actor=actor, rationale=rationale, request_id=request_id)
    if latest:
        for cfg in conn.execute("select id from config where brand_profile_id = %s", (latest["id"],)).fetchall():
            audit.update(conn, "config", {"id": cfg["id"]}, {"brand_profile_id": row["id"]}, actor=actor,
                         rationale=f"brand profile version {version}", request_id=request_id)
    return row


def confirm_competitor(conn, competitor_id, confirmed: bool, *, actor: str, rationale: str, request_id=None) -> dict:
    return audit.update(conn, "competitor", {"id": competitor_id}, {"confirmed": confirmed}, actor=actor,
                        rationale=rationale, request_id=request_id)
