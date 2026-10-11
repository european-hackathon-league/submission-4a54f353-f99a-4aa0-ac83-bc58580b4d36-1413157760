"""Write the texts of existing cases again with the current case writer prompt:
python -m app.cases.rewrite [--yes]

For cases whose texts were written by an older prompt version (title, short
title, summary, highlights, page). A case a person has worked on is never
touched: an edited page, an owner or a decision keeps its texts. Without
``--yes`` it only lists what it would rewrite.
"""

from __future__ import annotations

import json
import sys

from app import audit, db
from app.cases import writer
from app.cases.backfill import _members
from app.cases.cluster import _label
from app.rag import index

ACTOR = "agent:cases"


def _untouched(conn) -> list[dict]:
    """Cases with an unedited page that no person owns or has decided on."""
    return conn.execute(
        "select c.id, c.aspect_group, c.body_version, f.name as feature from case_item c"
        " join feature f on f.id = c.feature_id"
        " where not exists (select 1 from page_version p where p.object_type = 'case' and p.object_id = c.id"
        "                   and p.author not like 'agent:%%')"
        " and not exists (select 1 from case_vehicle v where v.case_id = c.id and v.owner_id is not null)"
        " and not exists (select 1 from decision d where d.object_type = 'case' and d.object_id = c.id"
        "                 and d.actor not like 'agent:%%')"
        " and exists (select 1 from signal s where s.case_id = c.id and s.validation = 'valid')"
        " order by c.id").fetchall()


def todo(conn) -> list[tuple[dict, list[dict]]]:
    return [(c, _members(conn, c["id"])) for c in _untouched(conn)]


def write(conn, cases: list[tuple[dict, list[dict]]], *, run_id=None) -> dict:
    """Store the new texts as the next page version. A case the LLM gave no answer
    for keeps its texts: the template never replaces a written page."""
    written, skipped = [], 0
    for c, members in cases:
        text, model, prompt_version = writer.write(_label(c["feature"], c["aspect_group"]), members)
        if model is None:
            skipped += 1
            continue
        row = audit.update(
            conn, "case_item", {"id": c["id"]},
            {"title": text.title, "short_title": text.short_title, "summary": text.summary,
             "highlights": text.highlights, "body_md": text.body_md},
            actor=ACTOR, run_id=run_id, expected_version=c["body_version"],
            rationale=f"case texts written again ({prompt_version}, {model})")
        audit.insert(conn, "page_version", {"object_type": "case", "object_id": c["id"],
                                            "version": row["body_version"], "body_md": text.body_md,
                                            "author": ACTOR},
                     actor=ACTOR, key=("object_type", "object_id", "version"), run_id=run_id)
        written.append(c["id"])
    return {"rewritten": written, "no LLM answer, kept": skipped}


def run(apply: bool = False) -> dict:
    with db.transaction() as conn:
        total = conn.execute("select count(*) n from case_item").fetchone()["n"]
        cases = todo(conn)
    result = {"cases": total, "to_rewrite": len(cases), "kept (a person worked on them)": total - len(cases)}
    if not apply:
        return result
    with db.transaction() as conn:
        run_id = conn.execute("insert into agent_run (agent, mode) values (%s, 'manual') returning id",
                              (ACTOR,)).fetchone()["id"]
    # The LLM writes outside the database transaction (cached on disk).
    writer.prefetch([(_label(c["feature"], c["aspect_group"]), m) for c, m in cases])
    with db.transaction() as conn:
        out = write(conn, cases, run_id=run_id)
        result.update({"rewritten": len(out["rewritten"]), "no LLM answer, kept": out["no LLM answer, kept"]})
        conn.execute("update agent_run set status = 'ok', ended_at = now(), params = %s where id = %s",
                     (json.dumps(result), run_id))
    # The search index follows the new pages; one short transaction per page (it embeds).
    for case_id in out["rewritten"]:
        with db.transaction() as conn:
            index.refresh_page(conn, "case", case_id)
    return result


if __name__ == "__main__":
    print(run(apply="--yes" in sys.argv[1:]))
