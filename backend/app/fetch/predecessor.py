"""On-demand predecessor research: the PM flags "removed vs older generation" on a
case, and the loop fetches the predecessor's reference pages so the claim gets
web evidence (CLAUDE.md, pipeline step 1: "only on demand when the PM flags a
removed feature").

flag(): PM decision in the request transaction: the case gets the tag
``removed_vs_predecessor`` (the score factor reads it) and a decision row.
research(): background run (its own agent_run, mode on_demand): fetch the
predecessor's pages with the registered adapters, store the records, let the
agents propose signals (vehicle_role predecessor via the gate), attach them to
cases and rescore the line. A general web search provider stays pluggable
(``SEARCH_SOURCES``); without one only the reference adapters run.
"""

from __future__ import annotations

import json
import traceback

from app import audit, db
from app.fetch.adapters.base import ADAPTERS, FetchContext
from app.fetch.http import PoliteClient
from app.fetch.subjects import Subject

TAG = "removed_vs_predecessor"
SEARCH_SOURCES = ("wikipedia",)


def flag(conn, case_id: str, vehicle: str, *, actor: str, rationale: str, request_id=None) -> dict:
    case = audit.fetch_for_update(conn, "case_item", {"id": case_id})
    tags = list(case["tags"] or [])
    if TAG not in tags:
        audit.update(conn, "case_item", {"id": case_id}, {"tags": [*tags, TAG]}, actor=actor, rationale=rationale,
                     request_id=request_id)
    audit.insert(conn, "decision", {"object_type": "case", "object_id": case_id, "vehicle": vehicle, "actor": actor,
                                    "action": "override", "rationale": f"removed vs predecessor: {rationale}"},
                 actor=actor, request_id=request_id)
    pred = conn.execute("select p.code, p.brand, p.series from vehicle v join vehicle p on p.code = v.predecessor_code"
                        " where v.code = %s", (vehicle,)).fetchone()
    return {"case_id": case_id, "vehicle": vehicle, "predecessor": pred["code"] if pred else None}


def predecessor_subject(conn, vehicle: str) -> Subject | None:
    row = conn.execute("select v.code, p.code as pred, p.brand, p.series from vehicle v join vehicle p"
                       " on p.code = v.predecessor_code where v.code = %s", (vehicle,)).fetchone()
    if row is None:
        return None
    # Evidence belongs to the current line (bmw_vehicle) and is about the older car (chassis code).
    return Subject(kind="vehicle", id=row["code"], brand=row["brand"], model=row["series"], bmw_vehicle=row["code"],
                   chassis_code=row["pred"], vehicle_role="predecessor", markets=[],
                   wikipedia_page=f"{row['brand']} {row['series']} ({row['pred']})",
                   terms=[{"term": f"{row['brand']} {row['series']} {row['pred']}", "language": "en",
                           "collision_safe": True, "scope": "any"}])


def research(case_id: str, vehicle: str, *, actor: str, client: PoliteClient | None = None) -> dict:
    from app.cases import pipeline
    from app.fetch.worker import run_agents
    from app.scoring import score

    with db.transaction() as conn:
        subject = predecessor_subject(conn, vehicle)
        run_id = conn.execute("insert into agent_run (agent, mode, params) values (%s, 'on_demand', %s) returning id",
                              ("fetch:predecessor", json.dumps({"case_id": case_id, "vehicle": vehicle,
                                                                "requested_by": actor}))).fetchone()["id"]
        sources = {r["source_id"]: r for r in conn.execute(
            "select * from source_registry where source_id = any(%s)", (list(SEARCH_SOURCES),)).fetchall()}
    summary = {"run_id": str(run_id), "records": 0, "signals": {}}
    status, error = "ok", None
    try:
        if subject is None:
            raise ValueError(f"{vehicle} has no predecessor in the vehicle table")
        ctx = FetchContext(client=client or PoliteClient(), backfill=True, run_id=run_id, max_pages=5)
        records, data = [], []
        for sid, source in sources.items():
            result = ADAPTERS[sid](ctx, source, subject, {})
            records += result.records
            data += result.data_records
        with db.transaction() as conn:
            new = audit.insert_many_returning(conn, "source_record", records, actor="fetch:predecessor",
                                              key="record_id", run_id=run_id, on_conflict_do_nothing=True,
                                              rationale=f"predecessor research for {case_id} ({vehicle})")
            summary["records"] = len(new)
            if new:
                summary["signals"] = run_agents(conn, new, run_id)
        summary["cases"] = pipeline.run(initial=False)
        with db.transaction() as conn:
            for cfg in conn.execute("select id from config where vehicle = %s and active", (vehicle,)).fetchall():
                score.score_config(conn, cfg["id"])
            audit.insert(conn, "notification", {
                "type": "new_evidence", "object_type": "case", "object_id": case_id, "vehicle": vehicle,
                "message": f"Predecessor research for {case_id}: {summary['records']} new records, "
                           f"{summary['signals'].get('valid', 0)} signals"}, actor="system", run_id=run_id)
    except Exception as exc:  # reported on the run and as a notification, never silent
        status, error = "failed", f"{exc!r}\n{traceback.format_exc()[-1500:]}"
        with db.transaction() as conn:
            audit.insert(conn, "notification", {"type": "run_failed", "object_type": "agent_run",
                                                "object_id": str(run_id), "vehicle": vehicle,
                                                "message": f"predecessor research for {case_id} failed: {exc}"[:300]},
                         actor="system", run_id=run_id)
    with db.transaction() as conn:
        sig = summary["signals"] or {}
        conn.execute("update agent_run set status = %s, ended_at = now(), error = %s, records_fetched = %s,"
                     " signals_valid = %s, signals_quarantined = %s where id = %s",
                     (status, error, summary["records"], sig.get("valid", 0), sig.get("quarantined", 0), run_id))
    summary["status"] = status
    return summary
