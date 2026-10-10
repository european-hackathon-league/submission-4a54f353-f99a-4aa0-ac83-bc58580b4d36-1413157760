"""Requirement actions by PMs and engineers: page edits (stale-save check),
re-target, cost edits, status flow, tombstone delete. Every action is audited and
recorded as a decision."""

from __future__ import annotations

from app import audit

STATUS_FLOW = {
    "in_discussion": {"draft"},
    "approved": {"draft", "in_discussion"},
    "rejected": {"draft", "in_discussion", "approved"},
    "draft": {"rejected", "in_discussion"},
}
CONTENT = ("title", "body_md")


class InvalidAction(ValueError):
    pass


def _decision(conn, rid: str, vehicle: str, actor: str, action: str, rationale: str | None, request_id=None):
    audit.insert(conn, "decision", {"object_type": "requirement", "object_id": rid, "vehicle": vehicle,
                                    "actor": actor, "action": action, "rationale": rationale},
                 actor=actor, request_id=request_id)


def _get(conn, rid: str) -> dict:
    row = audit.fetch_for_update(conn, "requirement", {"id": rid})
    if row["status"] == "deleted":
        raise InvalidAction(f"{rid} is deleted")
    return row


def save_page(conn, rid: str, body_md: str, expected_version: int, *, actor: str, request_id=None) -> dict:
    _get(conn, rid)
    row = audit.update(conn, "requirement", {"id": rid}, {"body_md": body_md}, actor=actor,
                       expected_version=expected_version, rationale="page edited", request_id=request_id)
    audit.insert(conn, "page_version", {"object_type": "requirement", "object_id": rid,
                                        "version": row["body_version"], "body_md": body_md, "author": actor},
                 actor=actor, key=("object_type", "object_id", "version"), request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "edit", "page edited", request_id)
    return row


def set_status(conn, rid: str, status: str, *, actor: str, rationale: str | None, request_id=None) -> dict:
    row = _get(conn, rid)
    if status not in STATUS_FLOW or row["status"] not in STATUS_FLOW[status]:
        raise InvalidAction(f"cannot move {rid} from {row['status']} to {status}")
    if status == "rejected" and not rationale:
        raise InvalidAction("rejecting needs a reason")
    if status == "approved" and not actor.startswith("pm:"):
        raise InvalidAction("only a PM approves requirements")
    out = audit.update(conn, "requirement", {"id": rid}, {"status": status}, actor=actor, rationale=rationale,
                       action="state_change", request_id=request_id)
    action = {"approved": "approve", "rejected": "reject"}.get(status, "edit")
    _decision(conn, rid, row["vehicle"], actor, action, rationale or f"status {status}", request_id)
    return out


def retarget(conn, rid: str, release_target: str, *, actor: str, rationale: str, request_id=None) -> dict:
    row = _get(conn, rid)
    out = audit.update(conn, "requirement", {"id": rid}, {"release_target": release_target}, actor=actor,
                       rationale=rationale, request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "retarget", rationale, request_id)
    return out


def set_cost(conn, rid: str, cost_size: str, *, actor: str, rationale: str, request_id=None) -> dict:
    row = _get(conn, rid)
    source = "engineering" if actor.startswith("engineer:") else "pm"
    out = audit.update(conn, "requirement", {"id": rid}, {"cost_size": cost_size, "cost_source": source},
                       actor=actor, rationale=rationale, request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "edit", f"cost {cost_size}: {rationale}", request_id)
    return out


def set_manual_rank(conn, rid: str, rank: int | None, *, actor: str, reason: str | None, request_id=None) -> dict:
    if rank is not None and not reason:
        raise InvalidAction("a manual rank needs a reason")
    row = _get(conn, rid)
    out = audit.update(conn, "requirement", {"id": rid},
                       {"manual_rank": rank, "manual_rank_reason": reason if rank is not None else None},
                       actor=actor, rationale=reason or "manual rank cleared", request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "reorder", reason or "manual rank cleared", request_id)
    return out


def board(conn, profile_id, statuses=("draft", "in_discussion", "approved")) -> list[dict]:
    """Requirements of the profile's vehicle ranked for THIS weight profile:
    rank = current case score under the profile x the profile's cost multiplier
    (computed on read; ``requirement.rank_score`` is only the default-profile cache).
    A market-specific requirement uses its own market's config only."""
    profile = conn.execute(
        "select p.*, c.vehicle, c.market from weight_profile p join config c on c.id = p.config_id where p.id = %s",
        (profile_id,)).fetchone()
    rows = conn.execute(
        "select r.id, r.case_id, r.market, r.title, r.status, r.cost_size, r.cost_source, r.release_target,"
        " r.evidence_level, r.manual_rank, r.manual_rank_reason, s.score as case_score"
        " from requirement r left join case_score_current s"
        " on s.case_id = r.case_id and s.weight_profile_id = %s"
        " where r.vehicle = %s and r.status = any(%s) and (r.market is null or r.market = %s)",
        (profile_id, profile["vehicle"], list(statuses), profile["market"])).fetchall()
    from app.scoring.rank import requirement_rank

    for r in rows:
        r["rank_score"] = requirement_rank(r["case_score"], r["cost_size"], profile["cost_multipliers"])
        r["case_score"] = float(r["case_score"]) if r["case_score"] is not None else None
    manual = sorted((r for r in rows if r["manual_rank"] is not None), key=lambda r: r["manual_rank"])
    auto = sorted((r for r in rows if r["manual_rank"] is None), key=lambda r: -(r["rank_score"] or 0))
    ordered = manual + auto
    for i, r in enumerate(ordered, start=1):
        r["rank"] = i
    return ordered


def delete(conn, rid: str, *, actor: str, rationale: str, request_id=None) -> dict:
    """Tombstone: content purged, row and history kept."""
    if not rationale:
        raise InvalidAction("deleting needs a reason")
    row = _get(conn, rid)
    out = audit.tombstone(conn, "requirement", {"id": rid}, CONTENT, actor=actor, rationale=rationale,
                          extra_changes={"status": "deleted"}, request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "delete", rationale, request_id)
    return out
