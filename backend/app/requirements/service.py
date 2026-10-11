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
LIVE = ("draft", "in_discussion", "approved", "signed_off", "planned", "exported")


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
        "select r.id, r.case_id, r.vehicle, r.market, r.title, r.status, r.cost_size, r.cost_source,"
        " r.release_target, r.evidence_level, r.manual_rank, r.manual_rank_reason, r.short_title, r.description,"
        " r.description_parts, r.metrics, r.provenance, r.shape_answers, r.market_scope, r.variant_of,"
        " r.change_depth, r.brief_id, r.drafter, r.cost_band, r.cost_note, r.cost_price_eur, r.owner_id,"
        " r.sent_back_reason, r.acceptance_criteria, r.assumptions, r.body_version, r.created_at, r.updated_at,"
        " s.score as case_score"
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


# ---- structured content, versions, sign-off flow (the PM workspace) ----------------------

# action -> (statuses it may start from, resulting status)
ACTIONS = {
    "discuss": ({"draft"}, "in_discussion"),
    "approve": ({"draft", "in_discussion"}, "approved"),
    "send_back": ({"approved"}, "in_discussion"),
    "sign_off": ({"approved"}, "signed_off"),
    "plan": ({"signed_off"}, "planned"),
    "export": ({"planned"}, "exported"),
    "reject": ({"draft", "in_discussion", "approved", "signed_off", "planned"}, "rejected"),
    "restore": ({"rejected"}, "draft"),
}
HEAD_ONLY = {"send_back", "sign_off"}
DECISION = {"discuss": "edit", "export": "export"}


def can_edit(user, row: dict) -> bool:
    """The owner or the head; a requirement nobody owns is open to the PMs and engineers of its line."""
    if user.is_head:
        return True
    if row["owner_id"] is None:
        return user.role == "engineer" or (user.role == "pm" and user.owns(row["vehicle"]))
    return str(row["owner_id"]) == user.id


def _require_editor(user, row: dict) -> None:
    if not can_edit(user, row):
        raise PermissionError(f"only the owner changes {row['id']}")


def apply_version(conn, rid: str, fields: dict, *, cause: str, actor: str, rationale: str,
                  expected_version: int | None = None, feedback_id: str | None = None, request_id=None) -> dict:
    """A new version of the content: the structured fields change, the page is
    rendered again from them, the version goes up and the snapshot is kept."""
    from app.requirements import generator

    unknown = set(fields) - set(generator.SNAPSHOT_FIELDS) - {"change_depth"}
    if unknown:
        raise InvalidAction(f"fields {sorted(unknown)} are not part of a requirement version")
    row = _get(conn, rid)
    fields = dict(fields)
    if "description" in fields and "description_parts" not in fields:
        fields["description_parts"] = generator.sentences(fields["description"])
    if not any(row[k] != v for k, v in fields.items()):
        return row
    case = conn.execute("select id, title from case_item where id = %s", (row["case_id"],)).fetchone()
    fields["body_md"] = generator.render_body({**row, **fields}, case)
    out = audit.update(conn, "requirement", {"id": rid}, fields, actor=actor, rationale=rationale,
                       expected_version=row["body_version"] if expected_version is None else expected_version,
                       request_id=request_id)
    audit.insert(conn, "page_version", {"object_type": "requirement", "object_id": rid,
                                        "version": out["body_version"], "body_md": out["body_md"], "author": actor},
                 actor=actor, key=("object_type", "object_id", "version"), request_id=request_id)
    audit.insert(conn, "requirement_version", {
        "requirement_id": rid, "version": out["body_version"], "snapshot": generator.snapshot(out), "cause": cause,
        "feedback_id": feedback_id, "rationale": rationale, "created_by": actor},
        actor=actor, key=("requirement_id", "version"), request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "edit" if cause == "edit" else "new_version",
              f"version {out['body_version']} ({cause}): {rationale}", request_id)
    return out


def patch(conn, rid: str, user, fields: dict, rationale: str, *, expected_version: int | None = None,
          request_id=None) -> dict:
    """A PM or engineer edits the SMART fields; every edit needs a reason and makes a version."""
    if not rationale:
        raise InvalidAction("an edit needs a reason")
    row = _get(conn, rid)
    _require_editor(user, row)
    fields = {k: v for k, v in fields.items() if v is not None}
    if "cost_size" in fields and "cost_source" not in fields and fields["cost_size"] != row["cost_size"]:
        fields["cost_source"] = "engineering" if user.role == "engineer" else "pm"
    return apply_version(conn, rid, fields, cause="edit", actor=user.actor, rationale=rationale,
                         expected_version=expected_version, request_id=request_id)


def transition(conn, rid: str, user, action: str, rationale: str, *, open_issues: dict | None = None,
               request_id=None) -> dict:
    """discuss, approve (PM), send back / sign off (head of product line), plan, export, reject, restore."""
    from app.collab import people, plan

    if action not in ACTIONS:
        raise InvalidAction(f"unknown action {action}")
    if not rationale:
        raise InvalidAction(f"{action} needs a reason")
    row = _get(conn, rid)
    sources, target = ACTIONS[action]
    if action in HEAD_ONLY:
        if not user.is_head:
            raise PermissionError("only the head of product line does this")
    elif action == "export":
        if user.role not in ("pm", "admin"):
            raise PermissionError("only a PM exports requirements")
    else:
        _require_editor(user, row)
    if action == "approve" and user.role != "pm":
        raise PermissionError("only a PM approves requirements")
    if row["status"] not in sources:
        raise InvalidAction(f"cannot {action} {rid} in status {row['status']}")
    changes = {"status": target}
    if action == "send_back":
        changes["sent_back_reason"] = rationale
    if action == "approve":
        changes["sent_back_reason"] = None
    out = audit.update(conn, "requirement", {"id": rid}, changes, actor=user.actor, action="state_change",
                       rationale=rationale + (f" (open quality issues: {open_issues})" if open_issues else ""),
                       request_id=request_id)
    _decision(conn, rid, row["vehicle"], user.actor, DECISION.get(action, action), rationale, request_id)
    link, label = f"/requirements/{rid}", f"{rid} · {row['title']}"
    if action == "approve":
        boss = people.head(conn)
        people.notify(conn, boss["id"] if boss else None, "sign_off_request", "Sign-off requested", label, link,
                      actor=user.actor, object_type="requirement", object_id=rid, vehicle=row["vehicle"],
                      request_id=request_id)
    elif action in ("send_back", "sign_off"):
        people.notify(conn, row["owner_id"], "sent_back" if action == "send_back" else "sign_off_request",
                      f"{user.name} sent a requirement back" if action == "send_back" else "Requirement signed off",
                      f"{rid}: {rationale}" if action == "send_back" else label, link, actor=user.actor,
                      object_type="requirement", object_id=rid, vehicle=row["vehicle"], request_id=request_id)
    elif action == "plan":
        plan.link_requirement(conn, out, actor=user.actor, request_id=request_id)
    return out


def rework(conn, rid: str, *, actor: str, rationale: str, request_id=None) -> dict:
    """An alert sends an approved requirement back into discussion."""
    row = _get(conn, rid)
    if row["status"] not in ("approved", "signed_off", "planned"):
        return row
    out = audit.update(conn, "requirement", {"id": rid}, {"status": "in_discussion"}, actor=actor,
                       rationale=rationale, action="state_change", request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "edit", rationale, request_id)
    return out


def add_feedback(conn, rid: str, user, data: dict, *, request_id=None) -> dict:
    """PM context layer 4: what engineering or finance said and how the PM responds."""
    import uuid

    row = _get(conn, rid)
    _require_editor(user, row)
    fb = audit.insert(conn, "requirement_feedback", {
        "requirement_id": rid, "source": data["source"], "text": data["text"], "response": data["response"],
        "pm_rationale": data["rationale"], "signal_ids": [uuid.UUID(str(s)) for s in data.get("signal_ids") or []],
        "created_by": uuid.UUID(user.id)}, actor=user.actor, rationale=data["rationale"], request_id=request_id)
    _decision(conn, rid, row["vehicle"], user.actor, "feedback",
              f"{data['source']} feedback, PM response {data['response']}: {data['rationale']}", request_id)
    return fb


def save_check(conn, rid: str, engine: str, issues: list, trigger: str, *, request_id=None) -> dict:
    """Result of a quality-check run (warns, never blocks)."""
    row = _get(conn, rid)
    return audit.insert(conn, "requirement_check", {"requirement_id": rid, "version": row["body_version"],
                                                    "engine": engine, "issues": issues, "trigger": trigger},
                        actor="agent:quality_check", rationale=trigger, request_id=request_id)


def bundle(conn, rid: str) -> dict | None:
    """Everything the requirement page shows next to the row."""
    row = conn.execute("select * from requirement where id = %s", (rid,)).fetchone()
    if row is None:
        return None
    return {
        "versions": conn.execute("select * from requirement_version where requirement_id = %s order by version desc",
                                 (rid,)).fetchall(),
        "feedback": conn.execute("select * from requirement_feedback where requirement_id = %s"
                                 " order by created_at desc", (rid,)).fetchall(),
        "check": conn.execute("select * from requirement_check_current where requirement_id = %s", (rid,)).fetchone(),
        "questions": conn.execute("select * from open_question where (object_type = 'requirement' and object_id = %s)"
                                  " or (object_type = 'case' and object_id = %s) order by created_at",
                                  (rid, row["case_id"])).fetchall(),
        "alerts": conn.execute("select * from alert where requirement_id = %s order by created_at desc",
                               (rid,)).fetchall(),
        "proposals": conn.execute("select * from proposal where object_type = 'requirement' and object_id = %s"
                                  " order by created_at", (rid,)).fetchall(),
        "variants": conn.execute("select * from requirement where case_id = %s and id <> %s and status <> 'deleted'"
                                 " order by id", (row["case_id"], rid)).fetchall(),
        "brief": conn.execute("select * from series_brief where id = %s", (row["brief_id"],)).fetchone()
        if row["brief_id"] else None,
    }


def delete(conn, rid: str, *, actor: str, rationale: str, request_id=None) -> dict:
    """Tombstone: content purged, row and history kept."""
    if not rationale:
        raise InvalidAction("deleting needs a reason")
    row = _get(conn, rid)
    out = audit.tombstone(conn, "requirement", {"id": rid}, CONTENT, actor=actor, rationale=rationale,
                          extra_changes={"status": "deleted"}, request_id=request_id)
    _decision(conn, rid, row["vehicle"], actor, "delete", rationale, request_id)
    return out
