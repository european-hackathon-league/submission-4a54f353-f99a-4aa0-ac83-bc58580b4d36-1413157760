"""People and their workspace: who the PMs are, which config (vehicle x market) and
weight profile each one works with, and personal notifications."""

from __future__ import annotations

import uuid

from app import audit

USER_COLUMNS = ("id, name, role, vehicles, handle, pm_role, title, initials, color, region, attribute_scope,"
                " synthetic")
WORKSPACE_FIELDS = {"config_id", "weight_profile_id", "source_filter", "watchlist"}
REGION_MARKET = {"CN": "China"}


def users(conn) -> list[dict]:
    return conn.execute(f"select {USER_COLUMNS} from app_user order by pm_role nulls last, name").fetchall()


def head(conn) -> dict | None:
    """The head of product line (signs off, resolves escalations)."""
    return conn.execute(f"select {USER_COLUMNS} from app_user where pm_role = 'head' order by created_at"
                        " limit 1").fetchone()


def default_owner(conn, vehicle: str, scope: str = "global") -> uuid.UUID | None:
    """The PM of a line for a region: the China PM for scope CN, else the PM of the other markets."""
    row = conn.execute(
        "select id from app_user where role = 'pm' and %s = any(vehicles)"
        " order by (pm_role = 'vehicle_pm') desc nulls last, ((region = 'CN') = %s) desc, created_at limit 1",
        (vehicle, scope == "CN")).fetchone()
    return row["id"] if row else None


def _default_profile(conn, config_id):
    if config_id is None:
        return None
    row = conn.execute("select id from weight_profile where config_id = %s order by is_default desc, name limit 1",
                       (config_id,)).fetchone()
    return row["id"] if row else None


def workspace(conn, user) -> dict:
    """The stored workspace, or the default one: the user's line in their region's
    market (China for the China PM, else the market with the largest 2030 volume)."""
    row = conn.execute("select * from workspace where user_id = %s", (user.id,)).fetchone()
    if row and row["config_id"] and conn.execute("select 1 from config where id = %s and active",
                                                 (row["config_id"],)).fetchone():
        if not row["weight_profile_id"]:
            row["weight_profile_id"] = _default_profile(conn, row["config_id"])
        return row
    vehicles = list(user.vehicles)
    cfg = conn.execute(
        "select c.id from config c left join sales_volume s on s.vehicle = c.vehicle and s.market = c.market"
        " and s.year = 2030 where c.active and (cardinality(%s::text[]) = 0 or c.vehicle = any(%s::text[]))"
        " order by (c.market = %s) desc, s.units desc nulls last, c.name limit 1",
        (vehicles, vehicles, REGION_MARKET.get(user.region, ""))).fetchone()
    config_id = cfg["id"] if cfg else None
    base = row or {"user_id": uuid.UUID(user.id),
                   "source_filter": {"excludeSocial": False, "ownersOnly": False, "minTrust": "E"},
                   "watchlist": {"competitors": [], "topics": [], "domains": [], "languages": []}}
    return {**base, "config_id": config_id, "weight_profile_id": _default_profile(conn, config_id)}


def update_workspace(conn, user, changes: dict, *, request_id=None) -> dict:
    changes = {k: v for k, v in changes.items() if v is not None}
    unknown = set(changes) - WORKSPACE_FIELDS
    if unknown:
        raise ValueError(f"workspace fields {sorted(unknown)} cannot be changed")
    current = workspace(conn, user)
    config_id = changes.get("config_id", current["config_id"])
    if "config_id" in changes and "weight_profile_id" not in changes:
        changes["weight_profile_id"] = _default_profile(conn, config_id)
    profile_id = changes.get("weight_profile_id", current["weight_profile_id"])
    if profile_id and not conn.execute("select 1 from weight_profile where id = %s and config_id = %s",
                                       (profile_id, config_id)).fetchone():
        raise ValueError("the weight profile does not belong to the config")
    if conn.execute("select 1 from workspace where user_id = %s", (user.id,)).fetchone():
        audit.update(conn, "workspace", {"user_id": uuid.UUID(user.id)},
                     {**changes, "config_id": config_id, "weight_profile_id": profile_id}, actor=user.actor,
                     rationale="workspace updated", request_id=request_id)
    else:
        audit.insert(conn, "workspace", {"user_id": uuid.UUID(user.id), "config_id": config_id,
                                         "weight_profile_id": profile_id,
                                         **{k: v for k, v in changes.items() if k in ("source_filter", "watchlist")}},
                     actor=user.actor, key="user_id", rationale="workspace created", request_id=request_id)
    return workspace(conn, user)


def notify(conn, user_id, kind: str, title: str, message: str, link: str, *, actor: str,
           object_type: str | None = None, object_id: str | None = None, vehicle: str | None = None,
           request_id=None) -> None:
    """One person's inbox item (the bell); never sent to the person who caused it."""
    if user_id is None or actor.endswith(f":{user_id}"):
        return
    audit.insert(conn, "notification", {"type": kind, "user_id": user_id, "title": title, "message": message,
                                        "link": link, "actor": actor, "object_type": object_type,
                                        "object_id": object_id, "vehicle": vehicle},
                 actor=actor, request_id=request_id)
