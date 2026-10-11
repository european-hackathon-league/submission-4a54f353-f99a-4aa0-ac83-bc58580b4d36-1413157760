"""Card texts and tags for cases created before migration 0014:
python -m app.cases.backfill

New cases get their short title, summary, highlights, case type and change
depth when they are created. This fills them for existing cases: the card texts
from the case writer (LLM when a key is set, cached; else the template), the
tags from the tag step of the pipeline. Title and page are never touched (a PM
may have edited them), and tags set by a PM stay.
"""

from __future__ import annotations

import json

from app import audit, db
from app.cases import figures, pipeline, tags, writer
from app.cases.cluster import _label

ACTOR = "agent:cases"


def _members(conn, case_id: str) -> list[dict]:
    return conn.execute(
        "select s.id, s.kind, s.severity, s.market, s.vehicle, s.customer_ref, s.evidence_role, s.quote"
        " from signal s where s.case_id = %s and s.validation = 'valid' order by s.id", (case_id,)).fetchall()


def run() -> dict:
    with db.transaction() as conn:
        run_id = conn.execute("insert into agent_run (agent, mode) values (%s, 'manual') returning id",
                              (ACTOR,)).fetchone()["id"]
    with db.transaction() as conn:
        waiting = [(c, _members(conn, c["id"])) for c in conn.execute(
            "select c.id, c.aspect_group, f.name as feature from case_item c join feature f on f.id = c.feature_id"
            " where c.short_title is null and exists (select 1 from signal s where s.case_id = c.id"
            " and s.validation = 'valid') order by c.id").fetchall()]
    # The LLM writes the card texts before the write transaction opens (cached on disk).
    writer.prefetch([(_label(c["feature"], c["aspect_group"]), m) for c, m in waiting])
    with db.transaction() as conn:
        cases = conn.execute(
            "select c.id, c.aspect_group, c.short_title, c.case_type, c.change_depth, f.name as feature"
            " from case_item c join feature f on f.id = c.feature_id"
            " where (c.short_title is null or c.case_type is null or c.change_depth is null)"
            " and exists (select 1 from signal s where s.case_id = c.id and s.validation = 'valid')"
            " order by c.id").fetchall()
        texts = [(c, _members(conn, c["id"])) for c in cases if c["short_title"] is None]
        writer.prefetch([(_label(c["feature"], c["aspect_group"]), m) for c, m in texts])
        written = 0
        for c, members in texts:
            text, model, prompt_version = writer.write(_label(c["feature"], c["aspect_group"]), members)
            audit.update(conn, "case_item", {"id": c["id"]},
                         {"short_title": text.short_title, "summary": text.summary, "highlights": text.highlights},
                         actor=ACTOR, run_id=run_id,
                         rationale=f"card texts ({prompt_version}{', ' + model if model else ''})")
            written += 1
        th = pipeline.thresholds_for(conn)
        profile = conn.execute("select body_md from brand_profile order by version desc limit 1").fetchone()
        brand = profile["body_md"] if profile else ""
        todo = [c["id"] for c in cases if c["case_type"] is None or c["change_depth"] is None]
        figs = {cid: figures.compute(conn, cid, reach_cap=int(th.get("reach_cap", 100000))) for cid in todo}
        jobs = []
        for cid in todo:
            case = conn.execute("select * from case_item where id = %s", (cid,)).fetchone()
            feature = conn.execute("select name, car_area from feature where id = %s", (case["feature_id"],)).fetchone()
            for cv in conn.execute("select vehicle, release_fit_source from case_vehicle where case_id = %s",
                                   (cid,)).fetchall():
                if cv["release_fit_source"] != "pm":
                    jobs.append((case, feature, figs[cid], cv["vehicle"], brand))
        tags.prefetch(jobs)
        for cid in todo:
            pipeline.update_case(conn, cid, run_id=run_id, brand_profile=brand, fig=figs[cid])
        result = {"card_texts": written, "tagged": len(todo)}
        conn.execute("update agent_run set status = 'ok', ended_at = now(), params = %s where id = %s",
                     (json.dumps(result), run_id))
    return result


if __name__ == "__main__":
    print(run())
