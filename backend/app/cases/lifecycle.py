"""Case lifecycle per BMW line: observed -> open -> confirmed / rejected.

The rules only ever move a case from observed to open. Confirm and reject are PM
decisions (a decision row + audit event); rejected cases keep their reason.
"""

from __future__ import annotations

import datetime as dt

from app import audit

TRANSITIONS = {
    "open": {"observed"},
    "confirm": {"open"},
    "reject": {"observed", "open"},
    "reopen": {"rejected", "confirmed"},
}
TARGET = {"open": "open", "confirm": "confirmed", "reject": "rejected", "reopen": "open"}


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
                 {"state": "open", "state_reason": reason, "opened_by": reason.split(" ")[0]},
                 actor="agent:cases", rationale=reason, action="state_change", run_id=run_id)
    audit.insert(conn, "notification", {
        "type": "case_opened", "object_type": "case", "object_id": case_id, "vehicle": vehicle,
        "message": f"{case_id} opened for {vehicle}: {reason}",
    }, actor="agent:cases", run_id=run_id)
    return True


def transition(conn, case_id: str, vehicle: str, action: str, *, actor: str, rationale: str | None,
               request_id: str | None = None) -> dict:
    """PM action: open, confirm, reject, reopen."""
    if action not in TRANSITIONS:
        raise InvalidTransition(f"unknown action {action}")
    if action in ("reject", "reopen") and not rationale:
        raise InvalidTransition(f"{action} needs a reason")
    cv = audit.fetch_for_update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle})
    if cv["state"] not in TRANSITIONS[action]:
        raise InvalidTransition(f"cannot {action} a case in state {cv['state']}")
    changes = {"state": TARGET[action], "state_reason": rationale or cv["state_reason"]}
    if action == "open":
        changes["opened_by"] = actor
    if action == "confirm":
        changes["confirmed_at"] = dt.datetime.now(dt.timezone.utc)
        changes["flag_new_evidence"] = 0
    row = audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle}, changes, actor=actor,
                       rationale=rationale, action="state_change", request_id=request_id)
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle, "actor": actor,
                                    "action": action if action != "reopen" else "reopen", "rationale": rationale},
                 actor=actor, request_id=request_id)
    return row
