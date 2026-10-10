"""Case page edits (Notion-like body), with stale-save rejection and history."""

from __future__ import annotations

from app import audit


def save_case_page(conn, case_id: str, body_md: str, expected_version: int, *, actor: str,
                   title: str | None = None, request_id: str | None = None) -> dict:
    changes = {"body_md": body_md}
    if title:
        changes["title"] = title
    row = audit.update(conn, "case_item", {"id": case_id}, changes, actor=actor, expected_version=expected_version,
                       rationale="case page edited", request_id=request_id)
    audit.insert(conn, "page_version", {"object_type": "case", "object_id": case_id, "version": row["body_version"],
                                        "body_md": body_md, "author": actor},
                 actor=actor, key=("object_type", "object_id", "version"), request_id=request_id)
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "actor": actor, "action": "edit",
                                    "rationale": "page edited"}, actor=actor, request_id=request_id)
    return row


def set_tag(conn, case_id: str, field: str, value: str, *, actor: str, rationale: str, vehicle: str | None = None,
            request_id: str | None = None) -> dict:
    """PM override of an AI tag: brand_fit, segment (case) or release_fit (per vehicle)."""
    if field in ("brand_fit", "segment"):
        row = audit.update(conn, "case_item", {"id": case_id}, {field: value, f"{field}_source": "pm"}, actor=actor,
                           rationale=rationale, request_id=request_id)
    elif field == "release_fit":
        if not vehicle:
            raise ValueError("release fit is set per vehicle")
        row = audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle},
                           {"release_fit": value, "release_fit_reason": rationale, "release_fit_source": "pm"},
                           actor=actor, rationale=rationale, request_id=request_id)
    else:
        raise ValueError(f"unknown tag {field}")
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle, "actor": actor,
                                    "action": "override", "rationale": f"{field} = {value}: {rationale}"},
                 actor=actor, request_id=request_id)
    return row
