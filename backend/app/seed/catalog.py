"""Load the fixed taxonomy (car areas, aspect groups, topics) and the study mapping.

The taxonomy comes from ``taxonomy/taxonomy.json`` and is not reviewed here: only
a human changes it, in ``taxonomy/``. A topic is stored as a ``feature`` row
(``feature.topic_id``). The study attribute -> topic mapping is still a proposal
in the review CSVs: a row whose ``confirmed`` column is ``yes`` loads as
confirmed, every other row as a candidate mapping (changes.md 2.2).
"""

from __future__ import annotations

import csv

from app import audit, taxonomy
from app.llm.embeddings import get_embedder
from app.seed.catalog_proposal import REVIEW_DIR

ACTOR = "seed:catalog"


def _read(name: str) -> list[dict]:
    with open(REVIEW_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _decision(row: dict, proposed_col: str) -> tuple[str, bool]:
    """(value, confirmed). Reviewers edit the proposed column in place and set confirmed = yes."""
    confirmed = (row.get("confirmed") or "").strip().lower() in ("yes", "y", "ok", "x")
    return (row.get(proposed_col) or "").strip(), confirmed


def load_car_areas(conn) -> int:
    tax = taxonomy.load()
    rationale = f"fixed taxonomy {tax['version']}"
    audit.insert_many(conn, "aspect_group", [{"code": a["id"], "description": a["description"]}
                                             for a in tax["aspect_groups"]],
                      actor=ACTOR, key="code", on_conflict_do_nothing=True, rationale=rationale)
    rows = [{"code": a["id"], "name": a["label"], "description": a["description"]} for a in tax["car_areas"]]
    return audit.insert_many(conn, "car_area", rows, actor=ACTOR, key="code", on_conflict_do_nothing=True,
                             rationale=rationale)


def load_features(conn) -> dict[str, str]:
    """Insert one feature per topic (plus the fallback) and the mappings; returns topic ID -> feature id."""
    tax = taxonomy.load()
    topics = tax["topics"]
    vectors = get_embedder().embed([f"{t['label']}. {t['hint'] or ''}".strip(". ") for t in topics])
    to_insert = [{"name": t["label"], "topic_id": t["id"], "car_area": t["car_area"], "description": t["hint"],
                  "hint": t["hint"], "vfc_level2": t["vfc_level2"], "taxonomy_version": tax["version"],
                  "status": "confirmed", "embedding": vec} for t, vec in zip(topics, vectors)]
    to_insert.append({"name": "Other (no topic fits)", "topic_id": taxonomy.OTHER, "car_area": None,
                      "description": "Fallback: goes to the taxonomy review queue", "hint": None, "vfc_level2": [],
                      "taxonomy_version": tax["version"], "status": "confirmed", "embedding": None})
    audit.insert_many(conn, "feature", to_insert, actor=ACTOR, on_conflict_do_nothing=True,
                      rationale=f"topic of the fixed taxonomy {tax['version']}")
    ids = topic_ids(conn)
    missing = [t["id"] for t in topics if t["id"] not in ids]
    if missing:
        raise RuntimeError(f"topics not stored: {missing[:5]}")

    mappings = [{"feature_id": ids[topic], "kind": "vfc_topic", "value": label,
                 "confirmed_by": f"taxonomy {tax['version']}"}
                for label, topic in tax["vfc_level2_to_topic"].items() if topic]
    for r in _read("study_attributes.csv"):
        topic, confirmed = _decision(r, "proposed_topic")
        if topic:
            mappings.append({"feature_id": ids[topic], "kind": "study_attribute", "value": r["attribute"],
                             "confirmed_by": "team" if confirmed else None})
    audit.insert_many(conn, "feature_mapping", mappings, actor=ACTOR, on_conflict_do_nothing=True,
                      rationale="VFC labels from the taxonomy; study attributes from the review CSV")
    return ids


def topic_ids(conn) -> dict[str, str]:
    rows = conn.execute("select id, topic_id from feature where topic_id is not null").fetchall()
    return {r["topic_id"]: str(r["id"]) for r in rows}


def mapping(conn, kind: str) -> dict[str, str]:
    rows = conn.execute("select value, feature_id from feature_mapping where kind = %s", (kind,)).fetchall()
    return {r["value"]: str(r["feature_id"]) for r in rows}
