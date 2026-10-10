"""Re-run unclassified BMW feedback rows through the LLM classifier:
python -m app.signals.reclassify

Applies to signals quarantined as "unclassified" and to heuristic verdicts
(``extra.kind_source = heuristic``). Needs an OpenAI key; without one it does
nothing. Changes go through the gate (``service.release``) or an audited update.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import audit, db
from app.llm.client import llm_available
from app.seed.classify import classify_rows
from app.signals import service

ACTOR = "agent:feedback-classifier"


@dataclass
class _Row:
    excel_row: int
    text: str


def run(conn) -> dict:
    if not llm_available():
        return {"skipped": "no OPENAI_API_KEY"}
    rows = conn.execute(
        "select id, quote, validation, extra from signal where record_id like 'bmw_feedback-%%'"
        " and (reject_reason like 'unclassified%%' or extra->>'kind_source' = 'heuristic')"
        " and validation <> 'deleted'"
    ).fetchall()
    verdicts = classify_rows([_Row(i, r["quote"]) for i, r in enumerate(rows)])
    counts = {"released": 0, "updated": 0, "still_unclassified": 0}
    for i, r in enumerate(rows):
        v = verdicts[i]
        if v.source != "llm" or v.kind is None:
            counts["still_unclassified"] += 1
            continue
        changes = {"kind": v.kind, "severity": v.severity, "summary": v.summary,
                   "extra": {**(r["extra"] or {}), "kind_source": "llm"}}
        if v.car_area:
            changes["car_area"] = [v.car_area]
        if r["validation"] == "quarantined":
            service.release(conn, r["id"], changes, actor=ACTOR, rationale="LLM classification of unclassified row")
            counts["released"] += 1
        else:
            audit.update(conn, "signal", {"id": r["id"]}, {**changes, "model": v.model,
                                                           "prompt_version": v.prompt_version},
                         actor=ACTOR, rationale="LLM classification replaces heuristic verdict")
            counts["updated"] += 1
    return counts


if __name__ == "__main__":
    with db.transaction() as conn:
        print(run(conn))
