"""Board ranking: by score, with the PM's manual overrides on top.

A manual rank (drag on the board) needs a reason and is a PM decision. Rescoring
never moves an overridden case; instead the board flags it when its score moved
noticeably since the previous computation, so the PM can look again.
"""

from __future__ import annotations

from app import audit

SCORE_MOVE_FLAG = 0.1
COST_MULTIPLIER_DEFAULT = {"S": 1.0, "M": 0.85, "L": 0.7}


def set_manual_rank(conn, case_id: str, vehicle: str, rank: int | None, *, actor: str, reason: str | None,
                    request_id: str | None = None) -> dict:
    if rank is not None and not reason:
        raise ValueError("a manual rank needs a reason")
    row = audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": vehicle},
                       {"manual_rank": rank, "manual_rank_reason": reason if rank is not None else None},
                       actor=actor, rationale=reason or "manual rank cleared", request_id=request_id)
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle, "actor": actor,
                                    "action": "reorder", "rationale": reason or "manual rank cleared"},
                 actor=actor, request_id=request_id)
    return row


def case_board(conn, profile_id, states=("open", "confirmed")) -> list[dict]:
    """Cases of the profile's vehicle, ordered: manual ranks first, then score."""
    rows = conn.execute(
        """
        with scores as (
            select case_id, score, rationale, factors, brand_multiplier, computed_at,
                   row_number() over (partition by case_id order by computed_at desc) as n
            from case_score where weight_profile_id = %(p)s
        )
        select c.id, c.title, c.short_title, c.summary, c.highlights, c.case_type, c.change_depth, c.feature_id,
               c.brand_fit, c.brand_fit_reason, c.brand_fit_source, c.brand_fit_confirmed_by, c.segment,
               c.body_version, c.created_at, c.created_by, greatest(c.updated_at, cv.updated_at) as updated_at,
               cv.vehicle, cv.state, cv.workflow, cv.outcome, cv.outcome_note, cv.owner_id, cv.co_owner_ids,
               cv.watcher_ids, cv.scope, cv.market_conflict,
               cv.release_fit, cv.manual_rank, cv.manual_rank_reason, cv.flag_new_evidence,
               s1.score, s1.rationale, s1.factors, s1.brand_multiplier, s2.score as previous_score
        from weight_profile wp
        join config cfg on cfg.id = wp.config_id
        join case_vehicle cv on cv.vehicle = cfg.vehicle
        join case_item c on c.id = cv.case_id
        left join scores s1 on s1.case_id = c.id and s1.n = 1
        left join scores s2 on s2.case_id = c.id and s2.n = 2
        where wp.id = %(p)s and cv.state = any(%(states)s)
        """,
        {"p": profile_id, "states": list(states)},
    ).fetchall()
    for r in rows:
        r["score"] = float(r["score"]) if r["score"] is not None else None
        prev = float(r["previous_score"]) if r["previous_score"] is not None else None
        r["score_moved"] = bool(r["manual_rank"] is not None and prev is not None and r["score"] is not None
                                and abs(r["score"] - prev) >= SCORE_MOVE_FLAG)
    manual = sorted((r for r in rows if r["manual_rank"] is not None), key=lambda r: r["manual_rank"])
    auto = sorted((r for r in rows if r["manual_rank"] is None), key=lambda r: -(r["score"] or 0))
    ordered = manual + auto
    for i, r in enumerate(ordered, start=1):
        r["rank"] = i
    return ordered


def requirement_rank(case_score: float | None, cost_size: str | None, cost_multipliers: dict | None) -> float | None:
    """Requirement rank = case score x cost multiplier of the requested profile.
    Unknown cost counts as 1.0 and is flagged ``cost_missing``, as in the SQL view
    ``requirement_rank`` (changes.md R0.2)."""
    if case_score is None:
        return None
    multipliers = cost_multipliers or COST_MULTIPLIER_DEFAULT
    factor = float(multipliers.get(cost_size, 1.0)) if cost_size else 1.0
    return round(float(case_score) * factor, 4)
