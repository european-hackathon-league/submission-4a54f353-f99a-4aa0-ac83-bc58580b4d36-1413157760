"""PM context layer 1: the series brief. One shared brief per BMW line, plus a
regional overlay per PM that holds only the fields that differ (each with a
reason). ``values`` and ``meta`` are stored in the UI contract's shape."""

from __future__ import annotations

import datetime as dt
import uuid

from app import audit

FIELDS = ("thesis", "targetCustomer", "marketWeights", "changeAmbition", "protectList", "dontFightOn", "givens",
          "costEnvelope")
MARKET_KEY = {"Europe": "EU", "USA": "US", "China": "CN", "Rest of World": "RoW"}


class Forbidden(PermissionError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def get(conn, brief_id: str) -> dict:
    row = conn.execute("select * from series_brief where id = %s", (brief_id,)).fetchone()
    if row is None:
        raise audit.NotFound(f"brief {brief_id} not found")
    return row


def shared(conn, vehicle: str) -> dict | None:
    return conn.execute("select * from series_brief where vehicle = %s and kind = 'shared'", (vehicle,)).fetchone()


def overlay(conn, vehicle: str, user_id) -> dict | None:
    return conn.execute("select * from series_brief where vehicle = %s and kind = 'overlay' and owner_id = %s",
                        (vehicle, user_id)).fetchone()


def prefill(conn, vehicle: str) -> tuple[dict, dict]:
    """What the data already answers: market weights (2030 sales), the protect list
    (topics customers like most) and the don't-fight-on list (brand profile). The
    PM confirms each; everything else is the PM's own answer."""
    values: dict = {}
    sales = conn.execute("select market, units from sales_volume where vehicle = %s and year = 2030",
                         (vehicle,)).fetchall()
    total = sum(r["units"] or 0 for r in sales)
    if total:
        weights = {k: 0.0 for k in MARKET_KEY.values()}
        for r in sales:
            if r["market"] in MARKET_KEY:
                weights[MARKET_KEY[r["market"]]] = round((r["units"] or 0) / total, 2)
        values["marketWeights"] = weights
    liked = conn.execute(
        "select f.name, count(*) n from signal s join feature f on f.id = s.feature_id"
        " where s.vehicle = %s and s.kind = 'like' and s.validation = 'valid' and s.evidence_role = 'bmw_customer'"
        " group by f.name order by n desc, f.name limit 5", (vehicle,)).fetchall()
    if liked:
        values["protectList"] = [r["name"] for r in liked]
    brand = conn.execute("select do_not from brand_profile order by version desc limit 1").fetchone()
    if brand and brand["do_not"]:
        values["dontFightOn"] = list(brand["do_not"])
    return values, {"marketWeights": "data", "protectList": "data", "dontFightOn": "brand"}


def ensure_shared(conn, vehicle: str, owner_id, *, actor: str = "seed:briefs") -> dict:
    """The shared brief of a line, created once with the data pre-fill (unconfirmed)."""
    row = shared(conn, vehicle)
    if row:
        return row
    values, source = prefill(conn, vehicle)
    at = _now()
    meta = {k: {"source": source[k], "confirmed": False, "updatedAt": at, "updatedBy": str(owner_id)} for k in values}
    return audit.insert(conn, "series_brief", {"id": f"BRIEF-{vehicle}", "vehicle": vehicle, "kind": "shared",
                                               "region": "global", "owner_id": owner_id, "values": values,
                                               "meta": meta},
                        actor=actor, rationale="shared brief pre-filled from sales, liked topics and the brand profile")


def create_overlay(conn, vehicle: str, user, *, request_id=None) -> dict:
    base = shared(conn, vehicle)
    if base is None:
        raise audit.NotFound(f"no shared brief for {vehicle}")
    if user.pm_role != "vehicle_pm" or not user.owns(vehicle):
        raise Forbidden(f"only a PM of the {vehicle} creates a regional overlay")
    if str(base["owner_id"]) == user.id:
        raise ValueError("you own the shared brief; edit it directly")
    existing = overlay(conn, vehicle, user.id)
    if existing:
        return existing
    values, meta = {}, {}
    if user.region == "CN":
        values["marketWeights"] = {"EU": 0, "US": 0, "CN": 1, "RoW": 0}
        meta["marketWeights"] = {"source": "data", "confirmed": False, "updatedAt": _now(), "updatedBy": user.id,
                                 "reason": "China PM: your variants are judged on the Chinese market only."}
    n = conn.execute("select count(*) n from series_brief where vehicle = %s and kind = 'overlay'",
                     (vehicle,)).fetchone()["n"]
    return audit.insert(conn, "series_brief", {
        "id": f"BRIEF-{vehicle}-{user.region}" + (f"-{n + 1}" if n else ""), "vehicle": vehicle, "kind": "overlay",
        "region": user.region, "parent_id": base["id"], "owner_id": uuid.UUID(user.id), "values": values,
        "meta": meta}, actor=user.actor, rationale="regional overlay on the shared brief", request_id=request_id)


def conflicts(base: dict, over: dict) -> list[dict]:
    """Overlay fields that differ from the shared brief."""
    out = []
    for k in FIELDS:
        if k not in over["values"] or over["values"][k] == base["values"].get(k):
            continue
        resolution = (over["meta"].get(k) or {}).get("resolution") or {}
        if resolution.get("status") == "aligned":
            continue
        out.append({"field": k, "overlayId": over["id"], "sharedValue": base["values"].get(k),
                    "overlayValue": over["values"][k], "reason": (over["meta"].get(k) or {}).get("reason", ""),
                    "status": "regional_variant" if resolution.get("status") == "regional_variant" else "open"})
    return out


def resolve(conn, vehicle: str, user_id) -> dict | None:
    """The brief a PM drafts with: shared values with their overlay on top, and
    where each field came from. None until the shared brief answers every field."""
    base = shared(conn, vehicle)
    if base is None or any(k not in base["values"] for k in FIELDS):
        return None
    over = overlay(conn, vehicle, user_id)
    values = {**base["values"], **(over["values"] if over else {})}
    origin = {}
    for k in FIELDS:
        src = (base["meta"].get(k) or {}).get("source")
        origin[k] = "overlay" if over and k in over["values"] else (src if src in ("data", "brand") else "shared")
    confirmed = all((base["meta"].get(k) or {}).get("confirmed") for k in FIELDS) and (
        not over or all(m.get("confirmed") for m in over["meta"].values()))
    return {"series": vehicle, "region": over["region"] if over else "global", "sharedId": base["id"],
            "overlayId": over["id"] if over else None, "values": values, "origin": origin, "confirmed": confirmed,
            "conflicts": conflicts(base, over) if over else []}


def needs_onboarding(conn, user) -> bool:
    """A vehicle PM answers the brief first: the owner confirms the shared brief,
    every other PM of the line confirms their own overlay."""
    if user.pm_role != "vehicle_pm" or not user.vehicles:
        return False
    base = shared(conn, user.vehicles[0])
    if base is None:
        return True
    if str(base["owner_id"]) == user.id:
        return not all((base["meta"].get(k) or {}).get("confirmed") for k in FIELDS)
    over = overlay(conn, user.vehicles[0], user.id)
    return over is None or not all(m.get("confirmed") for m in over["meta"].values())


def _editor(brief: dict, user) -> None:
    if str(brief["owner_id"]) != user.id and not user.is_head:
        raise Forbidden(f"only the owner (or the head of product line) edits {brief['id']}")


def affected_requirements(conn, brief: dict, fields: list[str]) -> list[dict]:
    """Requirements drafted with this brief (or an overlay of it) whose provenance
    names one of the changed fields. They are listed for review, never edited."""
    if not fields:
        return []
    rows = conn.execute(
        "select r.id, r.title, r.provenance from requirement r left join series_brief b on b.id = r.brief_id"
        " where r.vehicle = %s and r.status not in ('rejected', 'exported', 'deleted')"
        " and (r.brief_id = %s or b.parent_id = %s)", (brief["vehicle"], brief["id"], brief["id"])).fetchall()
    out = []
    for r in rows:
        used = sorted({f for f in fields for p in (r["provenance"] or [])
                       if (p.get("ref") or "").startswith(f"brief.{f}")})
        if used:
            out.append({"requirementId": r["id"], "title": r["title"], "fields": used,
                        "summary": "Drafted with the changed brief fields: review it (no automatic redraft).",
                        "changes": [], "proposalId": None})
    return out


def changed_fields(brief: dict, values: dict) -> list[str]:
    return [k for k, v in values.items() if k in FIELDS and v is not None and brief["values"].get(k) != v]


def save(conn, brief_id: str, user, values: dict, reasons: dict, confirm: list[str] | None = None,
         clear: list[str] | None = None, *, request_id=None) -> tuple[dict, list[dict]]:
    brief = audit.fetch_for_update(conn, "series_brief", {"id": brief_id})
    _editor(brief, user)
    unknown = (set(values) | set(reasons) | set(confirm or []) | set(clear or [])) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown brief fields {sorted(unknown)}")
    base = get(conn, brief["parent_id"]) if brief["kind"] == "overlay" else brief
    new_values, meta, at = dict(brief["values"]), dict(brief["meta"]), _now()
    changed = []
    for k, v in values.items():
        if v is None:
            continue
        if brief["kind"] == "overlay":
            if v == base["values"].get(k):
                continue  # an overlay holds only what differs
            if not reasons.get(k) and not (meta.get(k) or {}).get("reason"):
                raise ValueError(f"say why {k} differs from the shared brief")
        if new_values.get(k) != v:
            changed.append(k)
        new_values[k] = v
        entry = {"source": "pm", "confirmed": True, "updatedAt": at, "updatedBy": user.id}
        reason = reasons.get(k) or (meta.get(k) or {}).get("reason")
        if reason:
            entry["reason"] = reason
        meta[k] = entry
    for k in confirm or []:
        if k in meta:
            meta[k] = {**meta[k], "confirmed": True, "updatedAt": at, "updatedBy": user.id}
        elif k in new_values:
            meta[k] = {"source": "pm", "confirmed": True, "updatedAt": at, "updatedBy": user.id}
    for k in clear or []:
        if brief["kind"] == "overlay" and k in new_values:
            new_values.pop(k)
            meta.pop(k, None)
            changed.append(k)
    if new_values == brief["values"] and meta == brief["meta"]:
        return brief, []
    impact = affected_requirements(conn, brief, changed)
    rationale = " · ".join(r for r in reasons.values() if r) or (
        "confirmed pre-filled answers" if confirm else "brief edited")
    row = audit.update(conn, "series_brief", {"id": brief_id},
                       {"values": new_values, "meta": meta, "version": brief["version"] + 1}, actor=user.actor,
                       rationale=rationale, request_id=request_id)
    return row, impact


def apply_field(conn, brief_id: str, field: str, value, user, *, source: str, reason: str | None,
                request_id=None) -> dict:
    """One accepted proposal (strategy doc or learning) sets one brief field."""
    if field not in FIELDS:
        raise ValueError(f"unknown brief field {field}")
    brief = audit.fetch_for_update(conn, "series_brief", {"id": brief_id})
    _editor(brief, user)
    base = get(conn, brief["parent_id"]) if brief["kind"] == "overlay" else brief
    values, meta = dict(brief["values"]), dict(brief["meta"])
    if brief["kind"] == "overlay" and value == base["values"].get(field):
        values.pop(field, None)
        meta.pop(field, None)
    else:
        values[field] = value
        meta[field] = {"source": source, "confirmed": True, "updatedAt": _now(), "updatedBy": user.id,
                       **({"reason": reason} if reason and brief["kind"] == "overlay" else {})}
    return audit.update(conn, "series_brief", {"id": brief_id},
                        {"values": values, "meta": meta, "version": brief["version"] + 1}, actor=user.actor,
                        rationale=reason or f"{field} set from an accepted proposal", request_id=request_id)


def all_conflicts(conn) -> list[dict]:
    out = []
    for over in conn.execute("select * from series_brief where kind = 'overlay'").fetchall():
        for c in conflicts(get(conn, over["parent_id"]), over):
            out.append({**c, "series": over["vehicle"], "ownerId": str(over["owner_id"])})
    return sorted(out, key=lambda c: c["status"] != "open")


def resolve_conflict(conn, brief_id: str, field: str, resolution: str, rationale: str, user, *,
                     request_id=None) -> dict:
    """The head accepts a regional difference, or aligns the overlay to the shared brief."""
    from app.collab import people

    if not user.is_head:
        raise Forbidden("only the head of product line resolves brief conflicts")
    over = audit.fetch_for_update(conn, "series_brief", {"id": brief_id})
    if over["kind"] != "overlay" or field not in over["values"]:
        raise audit.NotFound(f"no regional difference on {field}")
    values, meta = dict(over["values"]), dict(over["meta"])
    if resolution == "align":
        values.pop(field)
        meta.pop(field, None)
    else:
        meta[field] = {**(meta.get(field) or {}), "resolution": {
            "status": "regional_variant", "by": user.id, "at": _now(), "note": rationale}}
    row = audit.update(conn, "series_brief", {"id": brief_id},
                       {"values": values, "meta": meta, "version": over["version"] + 1}, actor=user.actor,
                       rationale=rationale, action="resolve", request_id=request_id)
    people.notify(conn, over["owner_id"], "escalation",
                  f"Brief difference {'aligned' if resolution == 'align' else 'accepted as regional'}",
                  f"{field}: {rationale}", "/brief", actor=user.actor, request_id=request_id)
    return row
