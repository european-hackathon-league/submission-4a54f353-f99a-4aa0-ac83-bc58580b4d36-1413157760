"""Audit chain of one requirement: source record -> signal -> case -> score ->
PM decisions -> requirement -> approval. Every link is read from the stored rows
and the append-only audit log, nothing is inferred.

python -m app.reports.audit_chain REQ-G60-0930 [--signals 10]
writes backend/reports/audit_chain_<id>.md
"""

from __future__ import annotations

import argparse

from app import db
from app.config import BACKEND_DIR

EVENT_COLUMNS = "seq, at, actor, actor_type, action, object_type, object_id, rationale, run_id, request_id"


def _events(conn, object_type: str, object_ids: list[str]) -> list[dict]:
    if not object_ids:
        return []
    return conn.execute(f"select {EVENT_COLUMNS} from audit_event where object_type = %s and object_id = any(%s)"
                        " order by seq", (object_type, object_ids)).fetchall()


def collect(conn, requirement_id: str, max_signals: int = 10) -> dict:
    req = conn.execute("select * from requirement where id = %s", (requirement_id,)).fetchone()
    if req is None:
        from app.audit import NotFound

        raise NotFound(f"{requirement_id} not found")
    case = conn.execute("select c.id, c.title, c.aspect_group, c.created_by, f.topic_id, f.name as topic,"
                        " f.car_area from case_item c join feature f on f.id = c.feature_id where c.id = %s",
                        (req["case_id"],)).fetchone()
    cv = conn.execute("select * from case_vehicle where case_id = %s and vehicle = %s",
                      (req["case_id"], req["vehicle"])).fetchone()
    where = "s.case_id = %s and s.validation = 'valid' and (s.vehicle = %s or s.vehicle is null)"
    params: list = [req["case_id"], req["vehicle"]]
    if req["market"]:
        where += " and s.market = %s"
        params.append(req["market"])
    counts = conn.execute(f"select s.evidence_role, count(*) n, count(distinct s.customer_ref) customers"
                          f" from signal s where {where} group by 1", params).fetchall()
    signals = conn.execute(
        "select s.id, s.kind, s.severity, s.summary, s.quote, s.market, s.evidence_role, s.classified_by,"
        " s.run_id, s.prompt_version, s.model, s.record_id, r.source_id, r.url, r.raw_ref, r.published_at,"
        f" r.fetched_at from signal s join source_record r using (record_id) where {where}"
        " order by (s.severity = 'defect') desc, s.published_at desc nulls last, s.id limit %s",
        [*params, max_signals]).fetchall()
    signal_ids = [str(s["id"]) for s in signals]
    record_ids = sorted({s["record_id"] for s in signals})
    runs = {str(r["id"]): r for r in conn.execute(
        "select id, agent, mode, started_at, status from agent_run where id = any(%s)",
        ([s["run_id"] for s in signals if s["run_id"]],)).fetchall()}
    scores = conn.execute(
        "select p.name as profile, p.is_default, c.market, s.score, s.brand_multiplier, s.factors, s.rationale,"
        " s.computed_at from case_score_current s join weight_profile p on p.id = s.weight_profile_id"
        " join config c on c.id = p.config_id where s.case_id = %s and c.vehicle = %s"
        + (" and c.market = %s" if req["market"] else "") + " order by c.market, p.is_default desc, p.name",
        [req["case_id"], req["vehicle"]] + ([req["market"]] if req["market"] else [])).fetchall()
    decisions = conn.execute(
        "select object_type, object_id, vehicle, actor, action, rationale, created_at from decision"
        " where (object_type = 'case' and object_id = %s and (vehicle = %s or vehicle is null))"
        " or (object_type = 'requirement' and object_id = %s) order by created_at",
        (req["case_id"], req["vehicle"], requirement_id)).fetchall()
    return {
        "requirement": {k: req[k] for k in ("id", "case_id", "vehicle", "market", "title", "status", "release_target",
                                            "evidence_level", "cost_size", "cost_source", "cost_price_eur",
                                            "rank_score", "model", "prompt_version", "created_at")},
        "case": case,
        "case_vehicle": cv,
        "evidence_counts": counts,
        "signals": [{**s, "run": runs.get(str(s["run_id"]))} for s in signals],
        "scores": scores,
        "decisions": decisions,
        "events": {
            "source_record": _events(conn, "source_record", record_ids),
            "signal": _events(conn, "signal", signal_ids),
            "case": _events(conn, "case", [req["case_id"]]),
            "case_vehicle": _events(conn, "case_vehicle", [f"{req['case_id']}/{req['vehicle']}"]),
            "requirement": _events(conn, "requirement", [requirement_id]),
        },
    }


def _cut(text: str | None, n: int = 160) -> str:
    text = (text or "").replace("\n", " ").replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


def render(chain: dict) -> str:
    r, case, cv = chain["requirement"], chain["case"], chain["case_vehicle"]
    lines = [f"# Audit chain: {r['id']}", "",
             f"Requirement **{r['id']}** ({r['status']}): {r['title']}  ",
             f"Line {r['vehicle']}{' / ' + r['market'] if r['market'] else ''}; release target {r['release_target']};"
             f" evidence level {r['evidence_level']}; cost {r['cost_size']} ({r['cost_source']});"
             f" generated by {r['prompt_version']}{' / ' + r['model'] if r['model'] else ''}.", "",
             "## 1. Source records and signals", ""]
    for c in chain["evidence_counts"]:
        lines.append(f"- {c['evidence_role']}: {c['n']} signals, {c['customers']} distinct authors")
    lines += ["", f"Sample of {len(chain['signals'])} signals (defects first), each with its stored source record:", "",
              "| Signal | Kind | Market | Quote | Source record | Source | Run | Classified |",
              "|---|---|---|---|---|---|---|---|"]
    for s in chain["signals"]:
        run = s["run"] or {}
        lines.append(f"| `{str(s['id'])[:8]}` | {s['kind']} / {s['severity']} | {s['market']} | \"{_cut(s['quote'], 120)}\""
                     f" | `{s['record_id']}` | {s['source_id']} {s['url'] or ''} | {run.get('agent', '')}"
                     f" | {s['classified_by']} |")
    lines += ["", "## 2. Case", "",
              f"**{case['id']}**: {case['title']} (topic {case['topic']} / {case['topic_id']}, aspect group"
              f" {case['aspect_group']}, car area {case['car_area']}; created by {case['created_by']}).  ",
              f"State for {cv['vehicle']}: **{cv['state']}** (opened by {cv['opened_by']}: {cv['state_reason']});"
              f" release fit {cv['release_fit']} ({cv['release_fit_source']}): {cv['release_fit_reason']};"
              f" confirmed at {cv['confirmed_at']}.", "", "## 3. Scores (current, per weight profile)", "",
              "| Market | Profile | Score | Rationale |", "|---|---|---|---|"]
    for s in chain["scores"]:
        lines.append(f"| {s['market']} | {s['profile']}{' (default)' if s['is_default'] else ''} | "
                     f"{float(s['score']):.3f} | {_cut(s['rationale'], 400)} |")
    lines += ["", "## 4. Decisions (human)", "", "| When | Actor | Object | Action | Rationale |",
              "|---|---|---|---|---|"]
    for d in chain["decisions"]:
        lines.append(f"| {d['created_at']:%Y-%m-%d %H:%M} | {d['actor']} | {d['object_type']} {d['object_id']} |"
                     f" {d['action']} | {_cut(d['rationale'])} |")
    lines += ["", "## 5. Audit events (append-only log)", "", "| Seq | When | Actor | Type | Action | Object | Rationale |",
              "|---|---|---|---|---|---|---|"]
    for kind in ("source_record", "signal", "case", "case_vehicle", "requirement"):
        for e in chain["events"][kind]:
            lines.append(f"| {e['seq']} | {e['at']:%Y-%m-%d %H:%M} | {e['actor']} | {e['actor_type']} | {e['action']} |"
                         f" {e['object_type']} {_cut(e['object_id'], 40)} | {_cut(e['rationale'])} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirement_id")
    parser.add_argument("--signals", type=int, default=10)
    args = parser.parse_args()
    with db.transaction() as conn:
        text = render(collect(conn, args.requirement_id, args.signals))
    out = BACKEND_DIR / "reports" / f"audit_chain_{args.requirement_id}.md"
    out.write_text(text, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
