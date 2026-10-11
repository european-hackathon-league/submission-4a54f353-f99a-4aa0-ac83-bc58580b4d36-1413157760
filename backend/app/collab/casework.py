"""The PM's work on a case for one BMW line: ownership, workflow and decisions,
stance (PM context layer 2), open questions, meeting notes, referrals and
escalations, the innovation pool, monitor alerts and comments.

A case is shared by every line it applies to; what a PM does belongs to
``case_vehicle``. Handing a case to another PM never copies it: the receiver
works the same case, for their own line.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

from app import audit
from app.cases import cluster, lifecycle
from app.collab import people
from app.collab.briefs import Forbidden

RESOLUTIONS = {"one_market": "Prioritize one market", "variant": "Build a market variant",
               "keep_both": "Keep both open", "take_over": "Head takes over", "return": "Return to owner"}


def _uid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def case_vehicle(conn, case_id: str, vehicle: str, *, lock: bool = False) -> dict:
    if lock:
        return audit.fetch_for_update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle})
    row = conn.execute("select * from case_vehicle where case_id = %s and vehicle = %s", (case_id, vehicle)).fetchone()
    if row is None:
        raise audit.NotFound(f"{case_id} does not apply to {vehicle}")
    return row


def can_edit(user, cv: dict) -> bool:
    """The owner, a co-owner or the head. A case nobody owns yet is open to the PMs of its line."""
    if user.is_head:
        return True
    if user.role != "pm":
        return False
    if cv["owner_id"] is None:
        return user.owns(cv["vehicle"])
    return _uid(user.id) == cv["owner_id"] or _uid(user.id) in cv["co_owner_ids"]


def require_editor(user, cv: dict) -> None:
    if not can_edit(user, cv):
        raise Forbidden(f"only the owner or a co-owner changes {cv['case_id']} for {cv['vehicle']}; "
                        "you can comment or watch")


def _take_ownership(conn, cv: dict, user, request_id=None) -> None:
    """The first PM who works an unowned case becomes its owner for the line."""
    if cv["owner_id"] is None and user.role == "pm" and not user.pm_role == "head":
        audit.update(conn, "case_vehicle", {"case_id": cv["case_id"], "vehicle": cv["vehicle"]},
                     {"owner_id": _uid(user.id)}, actor=user.actor, rationale="first PM to work the case",
                     request_id=request_id)


def assign(conn, case_id: str, vehicle: str, user, changes: dict, *, rationale: str | None = None,
           request_id=None) -> dict:
    """Owner, co-owners, watchers, scope. Watching needs no permission: anyone may add or remove themselves."""
    cv = case_vehicle(conn, case_id, vehicle, lock=True)
    allowed = {"owner_id", "co_owner_ids", "watcher_ids", "scope"}
    changes = {k: v for k, v in changes.items() if k in allowed and v is not None}
    for k in ("co_owner_ids", "watcher_ids"):
        if k in changes:
            changes[k] = [_uid(v) for v in changes[k]]
    if "owner_id" in changes:
        changes["owner_id"] = _uid(changes["owner_id"])
    only_watch = set(changes) <= {"watcher_ids"} and (
        set(changes.get("watcher_ids", [])) ^ set(cv["watcher_ids"])) <= {_uid(user.id)}
    if not only_watch:
        require_editor(user, cv)
    row = audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle}, changes, actor=user.actor,
                       rationale=rationale or ("watch changed" if only_watch else "case ownership changed"),
                       request_id=request_id)
    if not only_watch:
        audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle,
                                        "actor": user.actor, "action": "assign",
                                        "rationale": rationale or "ownership changed"},
                     actor=user.actor, request_id=request_id)
    return row


def transition(conn, case_id: str, vehicle: str, user, action: str, rationale: str | None, *,
               to_user: str | None = None, horizon: str | None = None, escalate: bool = False,
               request_id=None) -> dict:
    """A PM moves or decides a case for a line. Refer creates a referral, innovation
    adds the case to the pool; an escalation asks the head and changes nothing else."""
    title = conn.execute("select title from case_item where id = %s", (case_id,)).fetchone()
    if title is None:
        raise audit.NotFound(f"{case_id} not found")
    if action == "refer" and escalate:
        cv = case_vehicle(conn, case_id, vehicle)
        boss = people.head(conn)
        if boss is None:
            raise ValueError("no head of product line to escalate to")
        if not rationale:
            raise lifecycle.InvalidTransition("an escalation needs a reason")
        _referral(conn, "escalation", case_id, vehicle, user, boss["id"], rationale, request_id)
        people.notify(conn, boss["id"], "escalation", f"Escalation from {user.name}", title["title"],
                      f"/cases/{case_id}", actor=user.actor, object_type="case", object_id=case_id,
                      vehicle=vehicle, request_id=request_id)
        return cv
    cv = case_vehicle(conn, case_id, vehicle, lock=True)
    require_editor(user, cv)
    if action == "refer" and not to_user:
        raise lifecycle.InvalidTransition("refer needs the PM to hand the case to")
    row = lifecycle.transition(conn, case_id, vehicle, action, actor=user.actor, rationale=rationale,
                               request_id=request_id)
    _take_ownership(conn, cv, user, request_id)
    if action == "refer":
        receiver = _uid(to_user)
        _referral(conn, "referral", case_id, vehicle, user, receiver, rationale, request_id)
        if receiver not in cv["watcher_ids"]:
            audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle},
                         {"watcher_ids": [*cv["watcher_ids"], receiver]}, actor=user.actor,
                         rationale="the receiver of a referral watches the case", request_id=request_id)
        people.notify(conn, receiver, "referral", f"{user.name} referred a case to you", title["title"], "/inbox",
                      actor=user.actor, object_type="case", object_id=case_id, vehicle=vehicle,
                      request_id=request_id)
    if action == "innovation":
        if not conn.execute("select 1 from innovation_item where case_id = %s", (case_id,)).fetchone():
            audit.insert(conn, "innovation_item", {
                "case_id": case_id, "horizon": horizon or "next_gen", "pitch": rationale or "",
                "votes": [_uid(user.id)], "interested_vehicles": [vehicle], "created_by": _uid(user.id)},
                actor=user.actor, key="case_id", rationale=rationale, request_id=request_id)
    return case_vehicle(conn, case_id, vehicle) if action in ("refer", "innovation") else row


def _referral(conn, kind: str, case_id: str, vehicle: str, user, to_user, note: str, request_id=None) -> dict:
    row = audit.insert(conn, "referral", {"kind": kind, "case_id": case_id, "vehicle": vehicle,
                                          "from_user": _uid(user.id), "to_user": _uid(to_user), "note": note},
                       actor=user.actor, rationale=note, request_id=request_id)
    if kind == "escalation":
        audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle,
                                        "actor": user.actor, "action": "escalate", "rationale": note},
                     actor=user.actor, request_id=request_id)
    return row


def referrals(conn, user) -> list[dict]:
    return conn.execute(
        "select * from referral where %s or to_user = %s or from_user = %s order by created_at desc",
        (user.is_head, user.id, user.id)).fetchall()


def _ensure_line(conn, case_id: str, vehicle: str, owner, *, actor: str, rationale: str, request_id=None) -> dict:
    """The case applies to this line too, open and owned by ``owner`` (an existing owner gets a co-owner)."""
    cv = conn.execute("select * from case_vehicle where case_id = %s and vehicle = %s", (case_id, vehicle)).fetchone()
    owner = _uid(owner)
    key = {"case_id": case_id, "vehicle": vehicle}
    if cv is None:
        return audit.insert(conn, "case_vehicle", {**key, "state": "open", "workflow": "investigating",
                                                   "state_reason": rationale, "opened_by": actor, "owner_id": owner},
                            actor=actor, key=("case_id", "vehicle"), rationale=rationale, request_id=request_id)
    changes: dict = {}
    if cv["state"] != "open" or cv["outcome"]:
        changes.update(state="open", workflow="investigating", outcome=None, outcome_note=None,
                       state_reason=rationale)
    if cv["owner_id"] is None:
        changes["owner_id"] = owner
    elif cv["owner_id"] != owner and owner not in cv["co_owner_ids"]:
        changes["co_owner_ids"] = [*cv["co_owner_ids"], owner]
    return audit.update(conn, "case_vehicle", key, changes, actor=actor, rationale=rationale,
                        action="state_change", request_id=request_id) if changes else cv


def decide_referral(conn, referral_id: str, user, decision: str, rationale: str, resolution: str | None = None,
                    *, request_id=None) -> dict:
    ref = audit.fetch_for_update(conn, "referral", {"id": referral_id})
    case = conn.execute("select title from case_item where id = %s", (ref["case_id"],)).fetchone()
    receiver = ref["to_user"] == _uid(user.id) or (ref["kind"] == "escalation" and user.is_head)
    now = dt.datetime.now(dt.timezone.utc)
    link = f"/cases/{ref['case_id']}"
    if decision == "accept":
        if not receiver:
            raise Forbidden("only the receiver accepts a referral")
        if ref["status"] != "pending":
            raise lifecycle.InvalidTransition("the referral is not pending")
        # The receiver works the same case: for their own line, or (cross-series PM) for the sender's line.
        line = user.vehicles[0] if user.vehicles else ref["vehicle"]
        _ensure_line(conn, ref["case_id"], line, user.id, actor=user.actor,
                     rationale=f"accepted referral {ref['id']}: {rationale}", request_id=request_id)
        changes = {"status": "accepted", "linked_case_id": ref["case_id"], "linked_vehicle": line, "decided_at": now}
        people.notify(conn, ref["from_user"], "referral", f"{user.name} accepted your referral", case["title"], link,
                      actor=user.actor, object_type="case", object_id=ref["case_id"], request_id=request_id)
    elif decision == "decline":
        if not receiver:
            raise Forbidden("only the receiver declines a referral")
        changes = {"status": "declined", "decline_reason": rationale, "decided_at": now}
        cv = conn.execute("select * from case_vehicle where case_id = %s and vehicle = %s",
                          (ref["case_id"], ref["vehicle"])).fetchone()
        if cv and cv["state"] == "open" and cv["outcome"] == "referred":
            audit.update(conn, "case_vehicle", {"case_id": ref["case_id"], "vehicle": ref["vehicle"]},
                         {"outcome": None, "outcome_note": None, "workflow": "ready"}, actor=user.actor,
                         rationale=f"referral declined: {rationale}", action="state_change", request_id=request_id)
        people.notify(conn, ref["from_user"], "referral", f"{user.name} declined your referral", rationale, link,
                      actor=user.actor, object_type="case", object_id=ref["case_id"], request_id=request_id)
    elif decision == "escalate":
        boss = people.head(conn)
        if boss is None:
            raise ValueError("no head of product line to escalate to")
        changes = {"status": "escalated"}
        _referral(conn, "escalation", ref["case_id"], ref["vehicle"], user, boss["id"],
                  f"Referral {ref['id']} contested: {rationale}", request_id)
        people.notify(conn, boss["id"], "escalation", f"Escalation from {user.name}", case["title"], link,
                      actor=user.actor, object_type="case", object_id=ref["case_id"], request_id=request_id)
    elif decision == "resolve":
        if not user.is_head:
            raise Forbidden("only the head of product line resolves escalations")
        label = RESOLUTIONS.get(resolution or "return", "Decided")
        changes = {"status": "resolved", "resolution": f"{resolution or 'decided'}: {rationale}", "decided_at": now}
        audit.insert(conn, "decision", {"object_type": "case", "object_id": ref["case_id"], "vehicle": ref["vehicle"],
                                        "actor": user.actor, "action": "resolve",
                                        "rationale": f"{label}: {rationale}"}, actor=user.actor, request_id=request_id)
        cv = conn.execute("select owner_id, watcher_ids from case_vehicle where case_id = %s and vehicle = %s",
                          (ref["case_id"], ref["vehicle"])).fetchone() or {"owner_id": None, "watcher_ids": []}
        for uid in {ref["from_user"], cv["owner_id"], *cv["watcher_ids"]} - {None}:
            people.notify(conn, uid, "escalation", f"Escalation resolved: {label}", case["title"], link,
                          actor=user.actor, object_type="case", object_id=ref["case_id"], request_id=request_id)
    else:
        raise ValueError(f"unknown decision {decision}")
    return audit.update(conn, "referral", {"id": referral_id}, changes, actor=user.actor, rationale=rationale,
                        action=decision, request_id=request_id)


# ---- stance, questions, meeting notes, comments ---------------------------------------

def save_stance(conn, case_id: str, user, stance: dict, *, request_id=None) -> dict:
    """PM context layer 2. An override of the brief needs a reason."""
    if not conn.execute("select 1 from case_item where id = %s", (case_id,)).fetchone():
        raise audit.NotFound(f"{case_id} not found")
    if user.role != "pm":
        raise Forbidden("only a PM takes a stance on a case")
    overrides = {k: v for k, v in (stance.get("overrides") or {}).items() if v is not None}
    reasons = stance.get("override_reasons") or {}
    for k in overrides:
        if not reasons.get(k):
            raise ValueError(f"say why this case differs from your brief ({k})")
    fields = {"overrides": overrides, "override_reasons": reasons,
              "evidence_takes": stance.get("evidence_takes") or [], "inside": stance.get("inside") or [],
              "ambition": stance.get("ambition")}
    if "pending_shape" in stance:
        fields["pending_shape"] = stance["pending_shape"] or []
    key = {"case_id": case_id, "author_id": _uid(user.id)}
    rationale = " · ".join(r for r in reasons.values() if r) or "stance saved"
    if conn.execute("select 1 from case_stance where case_id = %s and author_id = %s", (case_id, user.id)).fetchone():
        return audit.update(conn, "case_stance", key, fields, actor=user.actor, rationale=rationale,
                            request_id=request_id)
    return audit.insert(conn, "case_stance", {**key, **fields}, actor=user.actor, key=("case_id", "author_id"),
                        rationale=rationale, request_id=request_id)


def stance(conn, case_id: str, user_id) -> dict | None:
    return conn.execute("select * from case_stance where case_id = %s and author_id = %s",
                        (case_id, user_id)).fetchone()


def _object_vehicle(conn, object_type: str, object_id: str) -> str | None:
    table = "case_item" if object_type == "case" else "requirement"
    row = conn.execute(f"select {'null::text' if object_type == 'case' else 'vehicle'} as vehicle from {table}"
                       " where id = %s", (object_id,)).fetchone()
    if row is None:
        raise audit.NotFound(f"{object_id} not found")
    return row["vehicle"]


def create_question(conn, object_type: str, object_id: str, user, data: dict, *, vehicle: str | None = None,
                    request_id=None) -> dict:
    found = _object_vehicle(conn, object_type, object_id)
    return audit.insert(conn, "open_question", {
        "object_type": object_type, "object_id": object_id, "vehicle": found or vehicle,
        "addressee": data["addressee"], "contact": data.get("contact"), "question": data["question"],
        "why": data.get("why") or "", "due_date": data.get("due_date"), "asked_by": _uid(user.id),
        "fields_affected": data.get("fields_affected") or []}, actor=user.actor, request_id=request_id)


def update_question(conn, question_id: str, user, changes: dict, *, source: str = "manual",
                    request_id=None) -> dict:
    q = audit.fetch_for_update(conn, "open_question", {"id": question_id})
    allowed = {"addressee", "contact", "question", "why", "status", "answer", "due_date"}
    changes = {k: v for k, v in changes.items() if k in allowed and v is not None}
    if changes.get("answer") and "status" not in changes:
        changes["status"] = "answered"
    answered = changes.get("status") == "answered" and q["status"] != "answered"
    if answered:
        changes.update(answered_at=dt.datetime.now(dt.timezone.utc), answer_source=source)
    row = audit.update(conn, "open_question", {"id": question_id}, changes, actor=user.actor,
                       rationale="question answered" if answered else "question edited",
                       action="answer" if answered else "update", request_id=request_id)
    if answered:
        link = f"/{'cases' if q['object_type'] == 'case' else 'requirements'}/{q['object_id']}"
        people.notify(conn, q["asked_by"], "question_answered", f"{q['addressee'].replace('_', ' ')} answered",
                      (changes.get("answer") or "")[:160], link, actor=user.actor, object_type=q["object_type"],
                      object_id=q["object_id"], request_id=request_id)
    return row


def questions(conn, user, everything: bool = False) -> list[dict]:
    if everything:
        return conn.execute("select * from open_question order by created_at desc").fetchall()
    return conn.execute(
        "select q.* from open_question q where q.asked_by = %(u)s"
        " or (q.object_type = 'requirement' and exists (select 1 from requirement r where r.id = q.object_id"
        "     and r.owner_id = %(u)s))"
        " or (q.object_type = 'case' and exists (select 1 from case_vehicle cv where cv.case_id = q.object_id"
        "     and (cv.owner_id = %(u)s or %(u)s = any(cv.co_owner_ids))))"
        " order by q.created_at desc", {"u": _uid(user.id)}).fetchall()


def add_meeting_note(conn, case_id: str, user, data: dict, *, vehicle: str | None = None, request_id=None) -> dict:
    _object_vehicle(conn, "case", case_id)
    return audit.insert(conn, "meeting_note", {
        "case_id": case_id, "vehicle": vehicle, "title": data["title"], "held_on": data.get("held_on"),
        "attendees": data.get("attendees") or [], "text": data["text"], "created_by": _uid(user.id)},
        actor=user.actor, request_id=request_id)


def add_comment(conn, object_type: str, object_id: str, user, body: str, *, request_id=None) -> dict:
    """A comment; people named with @First or @First Last are notified."""
    _object_vehicle(conn, object_type, object_id)
    mentions = []
    for u in conn.execute("select id, name from app_user where id <> %s", (user.id,)).fetchall():
        first = u["name"].split(" ")[0]
        if re.search(rf"@{re.escape(u['name'])}\b|@{re.escape(first)}\b", body):
            mentions.append(u["id"])
    row = audit.insert(conn, "comment", {"object_type": object_type, "object_id": object_id,
                                         "author_id": _uid(user.id), "body": body, "mentions": mentions},
                       actor=user.actor, request_id=request_id)
    link = f"/{'cases' if object_type == 'case' else 'requirements'}/{object_id}"
    for uid in mentions:
        people.notify(conn, uid, "mention", f"{user.name} mentioned you", body[:120], link, actor=user.actor,
                      object_type=object_type, object_id=object_id, request_id=request_id)
    return row


# ---- innovation pool -----------------------------------------------------------------

def vote(conn, case_id: str, user, *, request_id=None) -> dict:
    item = audit.fetch_for_update(conn, "innovation_item", {"case_id": case_id})
    me = _uid(user.id)
    votes = [v for v in item["votes"] if v != me] if me in item["votes"] else [*item["votes"], me]
    interested = list(item["interested_vehicles"])
    if me in votes:
        interested += [v for v in user.vehicles if v not in interested]
    return audit.update(conn, "innovation_item", {"case_id": case_id},
                        {"votes": votes, "interested_vehicles": interested}, actor=user.actor,
                        rationale="innovation vote", request_id=request_id)


def pull(conn, case_id: str, vehicle: str, user, rationale: str, *, request_id=None) -> dict:
    """Pull an innovation into a line: the same case now applies to it, open and owned by the PM."""
    item = audit.fetch_for_update(conn, "innovation_item", {"case_id": case_id})
    if user.role != "pm" or not user.owns(vehicle):
        raise Forbidden(f"{vehicle} is not one of your lines")
    cv = _ensure_line(conn, case_id, vehicle, user.id, actor=user.actor,
                      rationale=f"pulled from the innovation pool: {rationale}", request_id=request_id)
    audit.update(conn, "innovation_item", {"case_id": case_id}, {
        "pulled_into": sorted({*item["pulled_into"], vehicle}),
        "interested_vehicles": sorted({*item["interested_vehicles"], vehicle})},
        actor=user.actor, rationale=rationale, request_id=request_id)
    title = conn.execute("select title from case_item where id = %s", (case_id,)).fetchone()["title"]
    people.notify(conn, item["created_by"], "referral", f"{user.name} pulled an innovation into {vehicle}", title,
                  f"/cases/{case_id}", actor=user.actor, object_type="case", object_id=case_id, vehicle=vehicle,
                  request_id=request_id)
    return cv


# ---- alerts --------------------------------------------------------------------------

def raise_alert(conn, case_id: str, vehicle: str, verdict: str, title: str, detail: str, signal_ids: list, *,
                actor: str = "agent:monitor", run_id=None) -> dict:
    """New evidence on a case the PM already confirmed: flagged to its owner, never applied."""
    cv = conn.execute("select owner_id, scope from case_vehicle where case_id = %s and vehicle = %s",
                      (case_id, vehicle)).fetchone() or {"owner_id": None, "scope": "global"}
    owner = cv["owner_id"] or people.default_owner(conn, vehicle, cv["scope"])
    req = conn.execute("select id from requirement where case_id = %s and vehicle = %s and status not in"
                       " ('rejected', 'deleted') order by id limit 1", (case_id, vehicle)).fetchone()
    row = audit.insert(conn, "alert", {"owner_id": owner, "case_id": case_id, "vehicle": vehicle,
                                       "requirement_id": req["id"] if req else None, "verdict": verdict,
                                       "title": title[:200], "detail": detail,
                                       "signal_ids": [_uid(s) for s in signal_ids]},
                       actor=actor, run_id=run_id, rationale=f"{verdict} for confirmed case {case_id}")
    people.notify(conn, owner, "alert", {"raises_the_bar": "Competitor raises the bar",
                                         "contradicts": "New evidence contradicts a decision"}.get(
        verdict, "New evidence supports a decision"), title[:200], f"/cases/{case_id}", actor=actor,
        object_type="case", object_id=case_id, vehicle=vehicle)
    return row


def alerts(conn, user, everything: bool = False) -> list[dict]:
    return conn.execute("select * from alert where %s or owner_id = %s order by created_at desc",
                        (everything or user.is_head, user.id)).fetchall()


def resolve_alert(conn, alert_id: str, user, status: str, rationale: str, *, request_id=None) -> dict:
    """Dismiss, or rework: the requirement goes back into discussion and the case is reopened."""
    from app.requirements import service

    a = audit.fetch_for_update(conn, "alert", {"id": alert_id})
    if a["owner_id"] != _uid(user.id) and not user.is_head:
        raise Forbidden("only the owning PM resolves an alert")
    if status == "rework":
        if a["requirement_id"]:
            service.rework(conn, a["requirement_id"], actor=user.actor, rationale=f"alert {alert_id}: {rationale}",
                           request_id=request_id)
        if a["case_id"] and a["vehicle"]:
            cv = conn.execute("select state, outcome from case_vehicle where case_id = %s and vehicle = %s",
                              (a["case_id"], a["vehicle"])).fetchone()
            if cv and cv["state"] == "open" and cv["outcome"] is None:
                lifecycle.transition(conn, a["case_id"], a["vehicle"], "start", actor=user.actor,
                                     rationale=f"alert {alert_id}: {rationale}", request_id=request_id)
    return audit.update(conn, "alert", {"id": alert_id}, {
        "status": status, "resolution": rationale, "resolved_by": _uid(user.id),
        "resolved_at": dt.datetime.now(dt.timezone.utc)}, actor=user.actor, rationale=rationale, action=status,
        request_id=request_id)


# ---- a case from loose signals, segments -----------------------------------------------

def create_case(conn, user, vehicle: str, signal_ids: list[str], title: str, summary: str, rationale: str, *,
                scope: str = "global", request_id=None) -> dict:
    """A PM starts a case from signals that belong to no case yet. The fixed taxonomy
    holds: all of them share one topic and one aspect group (grouping never crosses a topic)."""
    if user.role != "pm" or not user.owns(vehicle):
        raise Forbidden(f"{vehicle} is not one of your lines")
    members = conn.execute(
        "select s.id, s.feature_id, s.kind, s.severity, s.market, s.vehicle, s.customer_ref, s.evidence_role,"
        " s.quote, s.embedding, s.aspect_group, s.case_id, f.name as feature, f.topic_id"
        " from signal s left join feature f on f.id = s.feature_id"
        " where s.id = any(%s::uuid[]) and s.validation = 'valid'", (signal_ids,)).fetchall()
    if len(members) != len(set(signal_ids)):
        raise ValueError("some signals do not exist or are not valid")
    if any(m["case_id"] for m in members):
        raise ValueError("some signals already belong to a case; split that case instead")
    if any(m["feature_id"] is None or m["topic_id"] == "other" or m["embedding"] is None for m in members):
        raise ValueError("signals without a topic wait for classification and cannot start a case")
    groups = {(m["feature_id"], m["aspect_group"]) for m in members}
    if len(groups) != 1:
        raise ValueError("a case has one topic and one aspect group; these signals span "
                         f"{len(groups)} (grouping never crosses a topic)")
    case = cluster.create_case(conn, members[0]["feature_id"], members[0]["feature"], list(members),
                               aspect_group=members[0]["aspect_group"],
                               rationale=f"started by {user.actor} from {len(members)} signals: {rationale}")
    audit.update(conn, "case_item", {"id": case["id"]}, {"title": title, "summary": summary or case["summary"]},
                 actor=user.actor, rationale=rationale, request_id=request_id)
    _ensure_line(conn, case["id"], vehicle, user.id, actor=user.actor, rationale=rationale, request_id=request_id)
    audit.update(conn, "case_vehicle", {"case_id": case["id"], "vehicle": vehicle},
                 {"scope": scope, "workflow": "new"}, actor=user.actor, rationale=rationale, request_id=request_id)
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case["id"], "vehicle": vehicle,
                                    "actor": user.actor, "action": "create", "rationale": rationale},
                 actor=user.actor, request_id=request_id)
    return case


def segments(conn, case_id: str, vehicle: str | None = None) -> list[dict]:
    """Customer segments of a case, counted from BMW customer signals only:
    every market, and market x powertrain with at least 3 verbatims."""
    rows = conn.execute(
        "select market, powertrain, count(*) n from signal where case_id = %s and validation = 'valid'"
        " and evidence_role = 'bmw_customer' and (%s::text is null or vehicle = %s or vehicle is null)"
        " group by market, powertrain", (case_id, vehicle, vehicle)).fetchall()
    by_market: dict[str, int] = {}
    for r in rows:
        by_market[r["market"]] = by_market.get(r["market"], 0) + r["n"]
    out = [{"market": m, "powertrain": None, "verbatims": n} for m, n in by_market.items()]
    out += [{"market": r["market"], "powertrain": r["powertrain"], "verbatims": r["n"]} for r in rows
            if r["n"] >= 3 and r["powertrain"] != "unknown"]
    return sorted(out, key=lambda s: (-s["verbatims"], s["market"], s["powertrain"] or ""))
