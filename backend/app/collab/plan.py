"""The vehicle plan: the successor of a line, feature by feature. A feature is a
topic of the fixed taxonomy. Today's equipment comes from the option lists
(``vehicle_feature``), the linked requirements from the cases of the topic; only
the PM's decisions (keep / new / improve / remove, release, protected, note) are
stored, in ``planned_feature``."""

from __future__ import annotations

from app import audit
from app.collab.briefs import Forbidden

RANK = {"standard": 3, "optional": 2, "removed": 1, "absent": 0}
PLANNED = ("signed_off", "planned", "exported")


def ensure_plan(conn, vehicle: str) -> dict:
    row = conn.execute("select * from vehicle_plan where vehicle = %s", (vehicle,)).fetchone()
    if row:
        return row
    v = conn.execute("select code, series from vehicle where code = %s and is_current", (vehicle,)).fetchone()
    if v is None:
        raise audit.NotFound(f"no current vehicle line {vehicle}")
    owners = [r["id"] for r in conn.execute(
        "select id from app_user where role = 'pm' and pm_role = 'vehicle_pm' and %s = any(vehicles)"
        " order by created_at", (vehicle,)).fetchall()]
    return audit.insert(conn, "vehicle_plan", {"vehicle": vehicle, "successor_name": f"Successor of the {v['series']}"
                                               f" ({vehicle})", "owner_ids": owners},
                        actor="system", key="vehicle", rationale="vehicle plan opened")


def _today(rows: list[dict]) -> tuple[str, str]:
    """Best availability over the variants, and a one-line detail."""
    if not rows:
        return "absent", "Not in the option list (DE)"
    by_variant: dict[str, str] = {}
    for r in rows:
        if RANK[r["availability"]] > RANK.get(by_variant.get(r["variant"], ""), -1):
            by_variant[r["variant"]] = r["availability"]
    counts = {a: sum(1 for v in by_variant.values() if v == a) for a in RANK}
    best = max(by_variant.values(), key=RANK.get)
    prices = sorted({float(r["price_eur"]) for r in rows if r["price_eur"]})
    parts = [f"{a} on {n} variant{'s' if n != 1 else ''}" for a, n in counts.items() if n]
    return best, ", ".join(parts) + (f"; from {prices[0]:.0f} EUR" if prices else "") + " (DE option list)"


def plan(conn, vehicle: str) -> dict:
    head = ensure_plan(conn, vehicle)
    equipment: dict = {}
    for r in conn.execute("select feature_id, variant, availability, price_eur from vehicle_feature"
                          " where vehicle = %s and feature_id is not null", (vehicle,)).fetchall():
        equipment.setdefault(r["feature_id"], []).append(r)
    decisions = {r["feature_id"]: r for r in conn.execute(
        "select * from planned_feature where vehicle = %s", (vehicle,)).fetchall()}
    reqs: dict = {}
    for r in conn.execute(
            "select r.id, r.status, r.release_target, c.feature_id from requirement r join case_item c"
            " on c.id = r.case_id where r.vehicle = %s and r.status not in ('rejected', 'deleted') order by r.id",
            (vehicle,)).fetchall():
        reqs.setdefault(r["feature_id"], []).append(r)
    ids = set(equipment) | set(decisions) | set(reqs)
    features = []
    for f in conn.execute("select id, topic_id, name, car_area from feature where id = any(%s) and car_area is not"
                          " null order by car_area, name", (list(ids),)).fetchall():
        today, detail = _today(equipment.get(f["id"], []))
        d = decisions.get(f["id"]) or {}
        linked = reqs.get(f["id"], [])
        features.append({
            "id": f["topic_id"], "car_area": f["car_area"], "name": f["name"], "today": today,
            "today_detail": detail,
            "action": d.get("action") or ("new" if today == "absent" and linked else "improve" if linked else "keep"),
            "release": d.get("release") or (linked[0]["release_target"] if linked else None) or "next_gen",
            "protected": bool(d.get("is_protected")), "note": d.get("note") or "",
            "requirement_ids": [r["id"] for r in linked], "decided": bool(d),
        })
    return {**head, "features": features}


def _feature(conn, topic_id: str) -> dict:
    f = conn.execute("select id, name from feature where topic_id = %s", (topic_id,)).fetchone()
    if f is None:
        raise audit.NotFound(f"unknown topic {topic_id}")
    return f


def patch_feature(conn, vehicle: str, topic_id: str, user, changes: dict, rationale: str, *, request_id=None) -> dict:
    head = ensure_plan(conn, vehicle)
    if not user.is_head and user.id not in {str(o) for o in head["owner_ids"]} and not (
            user.role == "pm" and user.pm_role != "attribute_pm" and vehicle in user.vehicles):
        raise Forbidden("only the PMs of the series edit the vehicle plan")
    if not rationale:
        raise ValueError("a plan change needs a reason")
    f = _feature(conn, topic_id)
    changes = {k: v for k, v in changes.items() if k in ("action", "release", "is_protected", "note")
               and v is not None}
    key = {"vehicle": vehicle, "feature_id": f["id"]}
    if conn.execute("select 1 from planned_feature where vehicle = %s and feature_id = %s",
                    (vehicle, f["id"])).fetchone():
        return audit.update(conn, "planned_feature", key, changes, actor=user.actor, rationale=rationale,
                            request_id=request_id)
    current = next((x for x in plan(conn, vehicle)["features"] if x["id"] == topic_id), None)
    base = {"action": current["action"], "release": current["release"]} if current else {}
    return audit.insert(conn, "planned_feature", {**key, **base, **changes}, actor=user.actor,
                        key=("vehicle", "feature_id"), rationale=rationale, request_id=request_id)


def link_requirement(conn, requirement: dict, *, actor: str, request_id=None) -> None:
    """Planning a requirement records the decision on its feature (the topic of its case)."""
    ensure_plan(conn, requirement["vehicle"])
    f = conn.execute("select c.feature_id from case_item c where c.id = %s", (requirement["case_id"],)).fetchone()
    if conn.execute("select 1 from planned_feature where vehicle = %s and feature_id = %s",
                    (requirement["vehicle"], f["feature_id"])).fetchone():
        return
    listed = conn.execute("select 1 from vehicle_feature where vehicle = %s and feature_id = %s and availability in"
                          " ('standard', 'optional') limit 1", (requirement["vehicle"], f["feature_id"])).fetchone()
    audit.insert(conn, "planned_feature", {
        "vehicle": requirement["vehicle"], "feature_id": f["feature_id"], "action": "improve" if listed else "new",
        "release": requirement["release_target"] or "next_gen"}, actor=actor, key=("vehicle", "feature_id"),
        rationale=f"planned with {requirement['id']}", request_id=request_id)
