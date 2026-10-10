"""Load car areas, feature catalog and mappings from the review CSVs.

Reviewers correct the proposed columns in place; a row whose ``confirmed`` column
is ``yes`` loads as confirmed; every other row loads as ``candidate`` so the pipeline can run before
the team review, and the PM sees it is not confirmed yet (changes.md 2.2).
"""

from __future__ import annotations

import csv

from app import audit
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
    rows = [{"code": r["code"], "name": r["name"], "description": r["description"]} for r in _read("car_areas.csv")]
    return audit.insert_many(conn, "car_area", rows, actor=ACTOR, key="code", on_conflict_do_nothing=True,
                             rationale="fixed car-area list (review CSV)")


def load_features(conn) -> dict[str, str]:
    """Insert features and their mappings; returns feature name -> id."""
    rows = _read("features.csv")
    names = [r["feature"] for r in rows]
    texts = [f"{r['feature']}. {r['description']}".strip(". ") for r in rows]
    vectors = get_embedder().embed(texts)
    to_insert = []
    for r, vec in zip(rows, vectors):
        _, confirmed = _decision(r, "feature")
        to_insert.append({"name": r["feature"], "car_area": r["car_area"], "description": r["description"] or None,
                          "status": "confirmed" if confirmed else "candidate", "embedding": vec})
    audit.insert_many(conn, "feature", to_insert, actor=ACTOR, on_conflict_do_nothing=True,
                      rationale="feature catalog proposal (review CSV features.csv)")
    ids = {r["name"]: str(r["id"]) for r in conn.execute("select id, name from feature").fetchall()}
    missing = [n for n in names if n not in ids]
    if missing:
        raise RuntimeError(f"features not stored: {missing[:5]}")

    mappings = []
    for r in _read("vfc_topics.csv"):
        feature, confirmed = _decision(r, "proposed_feature")
        if feature:
            mappings.append({"feature_id": ids[feature], "kind": "vfc_topic", "value": r["vfc_level2"],
                             "confirmed_by": "team" if confirmed else None})
    for r in _read("study_attributes.csv"):
        feature, confirmed = _decision(r, "proposed_feature")
        if feature:
            mappings.append({"feature_id": ids[feature], "kind": "study_attribute", "value": r["attribute"],
                             "confirmed_by": "team" if confirmed else None})
    audit.insert_many(conn, "feature_mapping", mappings, actor=ACTOR, on_conflict_do_nothing=True,
                      rationale="mapping proposal (review CSVs)")
    return ids


def vfc_car_areas() -> dict[str, str]:
    """VFC level 2 topic -> car area code (confirmed value if the team corrected it)."""
    out = {}
    for r in _read("vfc_topics.csv"):
        out[r["vfc_level2"]] = r["proposed_car_area"]
    return out


def mapping(conn, kind: str) -> dict[str, str]:
    rows = conn.execute("select value, feature_id from feature_mapping where kind = %s", (kind,)).fetchall()
    return {r["value"]: str(r["feature_id"]) for r in rows}
