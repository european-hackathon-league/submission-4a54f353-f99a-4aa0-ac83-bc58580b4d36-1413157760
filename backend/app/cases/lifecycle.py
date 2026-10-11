"""Case lifecycle per BMW line: observed -> open -> confirmed / rejected.

The rules only ever move a case from observed to open. Everything else is a PM
decision (a decision row + audit event); rejected cases keep their reason.

While a case is open the PM moves it through ``workflow`` (new, investigating,
awaiting_answers, ready) and may decide it without closing it: ``outcome``
referred, innovation_pool or parked keeps the state open. Confirm and reject
change the state; ``outcome`` then follows it (trigger case_vehicle_derive).
"""

from __future__ import annotations

import datetime as dt

from app import audit

# action -> states it may start from
TRANSITIONS = {
    "open": {"observed"},
    "start": {"open"},
    "await": {"open"},
    "ready": {"open"},
    "confirm": {"open"},
    "reject": {"observed", "open"},
    "park": {"open"},
    "refer": {"open"},
    "innovation": {"open"},
    "reopen": {"rejected", "confirmed", "open"},
}
TARGET = {"open": "open", "confirm": "confirmed", "reject": "rejected", "reopen": "open"}
WORKFLOW = {"open": "new", "start": "investigating", "await": "awaiting_answers", "ready": "ready",
            "reopen": "investigating"}
OUTCOME = {"park": "parked", "refer": "referred", "innovation": "innovation_pool"}
NEEDS_REASON = {"reject", "reopen", "park", "refer", "innovation"}
DECISION = {"start": "workflow", "await": "workflow", "ready": "workflow"}


class InvalidTransition(ValueError):
    pass


def open_reason(vehicle_figures: dict, thresholds: dict) -> str | None:
    """The rule that opens an observed case, or None."""
    markets = vehicle_figures.get("markets", {})
    customers = vehicle_figures.get("distinct_customers", 0)
    defects = sum(m["defects"] for m in markets.values())
    equipment = vehicle_figures.get("equipment", {})
    competitor = vehicle_figures.get("competitor_evidence", {})
    if customers >= thresholds.get("open_min_customers", 10):
        return f"rule:min_customers ({customers} distinct customers)"
    if defects >= thresholds.get("open_min_defects", 3):
        return f"rule:defect ({defects} defect reports)"
    if equipment.get("removed"):
        return "rule:removed_feature (removed vs predecessor)"
    if competitor.get("likes_or_facts", 0) >= thresholds.get("competitor_gap_min_rivals", 1) and (
            not equipment.get("listed") or not equipment.get("standard")):
        return "rule:competitor_gap (rivals offer it, not standard here)"
    return None


def evaluate(conn, case_id: str, vehicle: str, vehicle_figures: dict, thresholds: dict, *, run_id=None) -> bool:
    cv = conn.execute("select state from case_vehicle where case_id = %s and vehicle = %s",
                      (case_id, vehicle)).fetchone()
    if not cv or cv["state"] != "observed":
        return False
    reason = open_reason(vehicle_figures, thresholds)
    if reason is None:
        return False
    audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle},
                 {"state": "open", "workflow": "new", "state_reason": reason, "opened_by": reason.split(" ")[0]},
                 actor="agent:cases", rationale=reason, action="state_change", run_id=run_id)
    audit.insert(conn, "notification", {
        "type": "case_opened", "object_type": "case", "object_id": case_id, "vehicle": vehicle,
        "message": f"{case_id} opened for {vehicle}: {reason}",
    }, actor="agent:cases", run_id=run_id)
    return True


def transition(conn, case_id: str, vehicle: str, action: str, *, actor: str, rationale: str | None,
               request_id: str | None = None) -> dict:
    """PM action on a case for one line: open, start, await, ready, confirm, reject,
    park, refer, innovation, reopen."""
    if action not in TRANSITIONS:
        raise InvalidTransition(f"unknown action {action}")
    if action in NEEDS_REASON and not rationale:
        raise InvalidTransition(f"{action} needs a reason")
    cv = audit.fetch_for_update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle})
    if cv["state"] not in TRANSITIONS[action]:
        raise InvalidTransition(f"cannot {action} a case in state {cv['state']}")
    if action == "reopen" and cv["state"] == "open" and cv["outcome"] is None:
        raise InvalidTransition("the case is open and undecided already")
    if action in ("start", "await", "ready", "confirm", "park", "refer", "innovation") and cv["outcome"]:
        raise InvalidTransition(f"the case is {cv['outcome']}; reopen it first")
    changes: dict = {}
    if action in TARGET:
        changes.update(state=TARGET[action], state_reason=rationale or cv["state_reason"])
    if action in WORKFLOW:
        changes["workflow"] = WORKFLOW[action]
    if action in OUTCOME:
        changes.update(outcome=OUTCOME[action], outcome_note=rationale)
    elif action == "confirm":
        changes.update(outcome="confirmed", outcome_note=rationale, workflow=None,
                       confirmed_at=dt.datetime.now(dt.timezone.utc), flag_new_evidence=0)
    elif action == "reject":
        changes.update(outcome="rejected", outcome_note=rationale, workflow=None)
    elif action == "reopen":
        changes.update(outcome=None, outcome_note=None)
    if action == "open":
        changes["opened_by"] = actor
    row = audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle}, changes, actor=actor,
                       rationale=rationale, action="state_change", request_id=request_id)
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle, "actor": actor,
                                    "action": DECISION.get(action, action),
                                    "rationale": rationale or f"workflow {WORKFLOW.get(action, action)}"},
                 actor=actor, request_id=request_id)
    return row
