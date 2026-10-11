"""Classify signals that are not classified yet with the LLM classifier:
python -m app.signals.reclassify

Applies to signals quarantined as "unclassified", to heuristic verdicts
(``extra.kind_source = heuristic``) and to every signal without a topic (BMW
label ``no_class_found`` or general enquiry, web signals stored without a key).
The case pipeline runs it before grouping. Needs an OpenAI key; without one it
does nothing and those signals wait. Changes go through the gate (``service.release``) or an audited update.
Kind and severity from a BMW Feedback Type are never replaced; a topic from a
BMW label is never replaced either.
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


def pending(conn) -> list[dict]:
    """Signals that still need a classification from their text."""
    return conn.execute(
        "select id, quote, validation, extra, feature_id from signal"
        " where (reject_reason like 'unclassified%%' or extra->>'kind_source' = 'heuristic'"
        " or (feature_id is null and validation = 'valid'))"
        " and validation <> 'deleted' and quote is not null order by id"
    ).fetchall()


def prefetch(rows: list[dict]) -> int:
    """Classify the texts outside any database transaction; ``run`` then reads the cache."""
    if llm_available() and rows:
        classify_rows([_Row(i, r["quote"]) for i, r in enumerate(rows)])
    return len(rows)


def run(conn) -> dict:
    if not llm_available():
        return {"skipped": "no OPENAI_API_KEY"}
    rows = pending(conn)
    verdicts = classify_rows([_Row(i, r["quote"]) for i, r in enumerate(rows)])
    counts = {"released": 0, "updated": 0, "still_unclassified": 0}
    topics = {r["topic_id"]: r for r in conn.execute(
        "select id, topic_id, car_area, taxonomy_version from feature where topic_id is not null").fetchall()}
    for i, r in enumerate(rows):
        v = verdicts[i]
        extra = dict(r["extra"] or {})
        labelled = extra.get("kind_source") == "bmw_label"
        if v.source != "llm" or (v.kind is None and not labelled):
            counts["still_unclassified"] += 1
            continue
        changes: dict = {}
        if not labelled:
            extra["kind_source"] = "llm"
            changes.update(kind=v.kind, severity=v.severity, summary=v.summary)
        gate_changes = dict(changes)
        if r["feature_id"] is None and v.topic_id in topics:
            if v.other_suggestion:
                extra["other_suggestion"] = v.other_suggestion
            topic = topics[v.topic_id]
            gate_changes.update(topic_id=v.topic_id, aspect_group=v.aspect_group, classified_by="llm",
                                classification_confidence=v.confidence)
            changes.update(feature_id=topic["id"], car_area=[topic["car_area"]] if topic["car_area"] else [],
                           aspect_group=v.aspect_group, classified_by="llm", classification_confidence=v.confidence,
                           taxonomy_version=topic["taxonomy_version"],
                           scope_tag="out_of_scope" if v.aspect_group == "price" else "in_scope")
        if r["validation"] == "quarantined":
            service.release(conn, r["id"], {**gate_changes, "extra": extra}, actor=ACTOR,
                            rationale="LLM classification of unclassified row")
            counts["released"] += 1
        else:
            audit.update(conn, "signal", {"id": r["id"]}, {**changes, "extra": extra, "model": v.model,
                                                           "prompt_version": v.prompt_version},
                         actor=ACTOR, rationale="LLM classification from the text")
            counts["updated"] += 1
    counts["taxonomy_review"] = service.queue_taxonomy_review(conn, actor=ACTOR)
    return counts


if __name__ == "__main__":
    with db.transaction() as conn:
        print(run(conn))
