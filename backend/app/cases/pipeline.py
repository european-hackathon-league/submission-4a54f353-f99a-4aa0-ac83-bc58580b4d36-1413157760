"""Case pipeline: python -m app.cases.pipeline [--initial]

LLM classification of signals without a topic -> grouping by topic + aspect
group (initial or incremental) -> figures -> tags ->
conflict flag -> open rules. One agent_run per run; every write audited.
"""

from __future__ import annotations

import argparse
import json

from app import audit, db
from app.cases import cluster, feature_link, figures, lifecycle, tags
from app.signals import reclassify

ACTOR = "agent:cases"


def thresholds_for(conn, vehicle: str | None = None) -> dict:
    """Thresholds of the vehicle's configs (the strictest open minimum wins when they differ)."""
    rows = conn.execute("select thresholds from config where active" + (" and vehicle = %s" if vehicle else ""),
                        (vehicle,) if vehicle else ()).fetchall()
    merged: dict = {}
    for r in rows:
        for k, v in (r["thresholds"] or {}).items():
            if k == "open_min_customers":
                merged[k] = max(merged.get(k, 0), v)
            else:
                merged.setdefault(k, v)
    return merged


def update_case(conn, case_id: str, *, run_id=None, brand_profile: str = "") -> dict:
    th = thresholds_for(conn)
    fig = figures.compute(conn, case_id, reach_cap=int(th.get("reach_cap", 100000)))
    audit.update(conn, "case_item", {"id": case_id}, {"figures": fig}, actor=ACTOR, run_id=run_id,
                 rationale="case figures recomputed")
    case = conn.execute("select * from case_item where id = %s", (case_id,)).fetchone()
    feature = conn.execute("select name, car_area from feature where id = %s", (case["feature_id"],)).fetchone()
    opened = 0
    for cv in conn.execute("select * from case_vehicle where case_id = %s", (case_id,)).fetchall():
        v = cv["vehicle"]
        vth = thresholds_for(conn, v) or th
        vf = fig["by_vehicle"].get(v, {})
        is_conflict, why = figures.conflict(vf.get("markets", {}), int(vth.get("conflict_min_customers", 5)),
                                            float(vth.get("conflict_negative_share", 0.6)))
        changes = {}
        if is_conflict != cv["market_conflict"]:
            changes["market_conflict"] = is_conflict
        if cv["release_fit_source"] != "pm":
            t = tags.propose(case, feature, fig, v, brand_profile)
            changes.update(release_fit=t["release_fit"], release_fit_reason=t["release_fit_reason"],
                           release_fit_source="ai")
            case_changes = {}
            if case["segment_source"] != "pm":
                case_changes.update(segment=t["segment"], segment_source="ai")
            if case["brand_fit_source"] != "pm" and t["brand_fit"]:
                case_changes.update(brand_fit=t["brand_fit"], brand_fit_source="ai")
            if case_changes:
                case = audit.update(conn, "case_item", {"id": case_id}, case_changes, actor=ACTOR, run_id=run_id,
                                    rationale=f"tags proposed ({t['source']})")
        if changes:
            audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": v}, changes, actor=ACTOR,
                         run_id=run_id, rationale=why or "release fit proposed")
        if is_conflict and not cv["market_conflict"]:
            audit.insert(conn, "notification", {
                "type": "market_conflict", "object_type": "case", "object_id": case_id, "vehicle": v,
                "message": f"{case_id} ({v}): market conflict. {why}",
            }, actor=ACTOR, run_id=run_id)
        opened += lifecycle.evaluate(conn, case_id, v, vf, vth, run_id=run_id)
    return {"opened": opened}


def run(initial: bool = False) -> dict:
    with db.transaction() as conn:
        run_id = conn.execute("insert into agent_run (agent, mode) values (%s, %s) returning id",
                              (ACTOR, "backfill" if initial else "incremental")).fetchone()["id"]
    with db.transaction() as conn:
        th = thresholds_for(conn)
        # Signals that still have no topic go through the LLM classifier (closed lists). Without an
        # OpenAI key they wait: a topic is never guessed.
        classified = reclassify.run(conn)
        sim = feature_link.threshold(th, "similarity_case", 0.55)
        if initial:
            grouped = cluster.initial(conn, sim, run_id=run_id,
                                      split_min=int(th.get("case_split_min_signals", cluster.SPLIT_MIN)))
        else:
            grouped = cluster.incremental(conn, sim, run_id=run_id)
        profile = conn.execute("select body_md from brand_profile order by version desc limit 1").fetchone()
        touched = [r["id"] for r in conn.execute(
            "select distinct c.id from case_item c join signal s on s.case_id = c.id"
            " where s.updated_at >= (select started_at from agent_run where id = %s)", (run_id,)).fetchall()]
        opened = sum(update_case(conn, cid, run_id=run_id, brand_profile=profile["body_md"] if profile else "")
                     ["opened"] for cid in touched)
        result = {"classify": classified, "cluster": grouped, "cases_updated": len(touched), "opened": opened,
                  "provider": feature_link.provider_key(), "similarity_case": sim}
        conn.execute("update agent_run set status = 'ok', ended_at = now(), params = %s where id = %s",
                     (json.dumps(result, default=str), run_id))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", action="store_true", help="cluster all unassigned signals from scratch")
    print(run(parser.parse_args().initial))
