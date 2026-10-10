"""Link signals to the feature catalog.

Signals that already carry a feature (BMW VFC topic mapping) keep it. Others are
matched by embedding their feature hint (or summary) against the catalog. No
match above the threshold:
- with a feature hint -> a candidate feature is proposed for the PM (notification);
- without one -> the signal stays unlinked (visible, not in any case). Generic
  statements ("great car") must not invent features.
"""

from __future__ import annotations

import numpy as np

from app import audit
from app.llm.embeddings import as_matrix, get_embedder

ACTOR = "agent:cases"


def provider_key() -> str:
    return get_embedder().name.split(":", 1)[0]


def threshold(thresholds: dict, name: str, default: float) -> float:
    value = thresholds.get(name, default)
    if isinstance(value, dict):
        return float(value.get(provider_key(), default))
    return float(value)


def _catalog(conn) -> tuple[list[dict], np.ndarray]:
    rows = conn.execute(
        "select id, name, car_area, embedding from feature where status <> 'rejected' and embedding is not null"
    ).fetchall()
    matrix = as_matrix([r["embedding"] for r in rows]) if rows else np.zeros((0, 1), dtype=np.float32)
    return rows, matrix


def link(conn, *, min_similarity: float, run_id=None) -> dict:
    signals = conn.execute(
        "select id, feature_hint, summary, car_area from signal where validation = 'valid' and feature_id is null"
        " and scope_tag = 'in_scope'"
    ).fetchall()
    if not signals:
        return {"linked": 0, "candidates": 0, "unlinked": 0}
    features, matrix = _catalog(conn)
    embedder = get_embedder()
    texts = [s["feature_hint"] or s["summary"] or "" for s in signals]
    vectors = embedder.embed(texts)
    sims = vectors @ matrix.T if len(features) else np.zeros((len(signals), 0))
    linked, candidates, unlinked = [], {}, 0
    for s, row in zip(signals, sims):
        best = int(np.argmax(row)) if row.size else -1
        if best >= 0 and row[best] >= min_similarity:
            linked.append(({"id": s["id"]}, {"feature_id": features[best]["id"],
                                             "extra_link": float(row[best])}))
        elif s["feature_hint"]:
            candidates.setdefault(s["feature_hint"].strip().capitalize(), []).append(s)
        else:
            unlinked += 1
    for key, changes in linked:
        sim = changes.pop("extra_link")
        audit.update(conn, "signal", key, changes, actor=ACTOR, run_id=run_id,
                     rationale=f"feature link by embedding similarity {sim:.2f} >= {min_similarity:.2f}")
    for name, group in candidates.items():
        area = (group[0]["car_area"] or ["general"])[0]
        vec = embedder.embed([name])[0]
        feature = audit.insert(conn, "feature", {"name": name, "car_area": area, "status": "candidate",
                                                 "description": "proposed from signal feature hints",
                                                 "embedding": vec},
                               actor=ACTOR, run_id=run_id, on_conflict_do_nothing=True,
                               rationale="no catalog feature above the similarity threshold")
        if feature is None:
            feature = conn.execute("select id from feature where name = %s", (name,)).fetchone()
        else:
            audit.insert(conn, "notification", {
                "type": "candidate_feature", "object_type": "feature", "object_id": str(feature["id"]),
                "message": f"New candidate feature '{name}' from {len(group)} signal(s) waits for review",
            }, actor=ACTOR, run_id=run_id)
        for s in group:
            audit.update(conn, "signal", {"id": s["id"]}, {"feature_id": feature["id"]}, actor=ACTOR,
                         run_id=run_id, rationale="linked to candidate feature")
    return {"linked": len(linked), "candidates": len(candidates), "unlinked": unlinked}
