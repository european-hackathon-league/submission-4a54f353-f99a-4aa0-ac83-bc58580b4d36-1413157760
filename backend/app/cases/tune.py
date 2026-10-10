"""Similarity threshold report for case clustering on the seed data:
python -m app.cases.tune

Clusters the valid, feature-linked signals in memory (no writes) for a range of
thresholds and reports, per threshold: number of cases, share of singletons,
median and largest case size, mean cosine to the case centroid (cohesion) and
the share of cases that would open by the customer rule. There is no labelled
ground truth for "same problem", so the report supports the choice; the PM can
merge or split afterwards. Report: backend/reports/clustering_thresholds.md.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

import numpy as np

from app import db
from app.cases.cluster import cluster_labels
from app.cases.pipeline import thresholds_for
from app.config import BACKEND_DIR
from app.llm.embeddings import as_matrix, get_embedder

REPORT = BACKEND_DIR / "reports" / "clustering_thresholds.md"


def evaluate(groups: dict, threshold: float, open_min: int) -> dict:
    sizes, cohesion, opened = [], [], 0
    for members in groups.values():
        vectors = as_matrix([m["embedding"] for m in members])
        labels = cluster_labels(vectors, threshold)
        for label in set(labels):
            idx = np.where(labels == label)[0]
            v = vectors[idx]
            centroid = v.mean(axis=0)
            centroid /= np.linalg.norm(centroid) or 1
            sizes.append(len(idx))
            cohesion.append(float((v @ centroid).mean()))
            per_vehicle = defaultdict(set)
            for i in idx:
                per_vehicle[members[i]["vehicle"]].add(members[i]["customer_ref"])
            opened += any(len(c) >= open_min for c in per_vehicle.values())
    return {
        "threshold": threshold, "cases": len(sizes),
        "singletons": sum(1 for s in sizes if s == 1) / len(sizes),
        "median_size": statistics.median(sizes), "max_size": max(sizes),
        "cohesion": statistics.fmean(cohesion), "would_open": opened,
    }


def run(thresholds: list[float]) -> list[dict]:
    with db.transaction() as conn:
        rows = conn.execute(
            "select feature_id, vehicle, customer_ref, embedding from signal where validation = 'valid'"
            " and feature_id is not null and embedding is not null and scope_tag = 'in_scope'"
        ).fetchall()
        open_min = int(thresholds_for(conn).get("open_min_customers", 10))
    groups = defaultdict(list)
    for r in rows:
        groups[r["feature_id"]].append(r)
    results = [evaluate(groups, t, open_min) for t in thresholds]
    lines = [
        "# Case clustering: threshold report",
        "",
        f"Embedding provider: `{get_embedder().name}`. Signals: {len(rows)} in {len(groups)} features. "
        f"Open rule: >= {open_min} distinct customers per line.",
        "",
        "| Cosine threshold | Cases | Singletons | Median size | Largest | Cohesion | Would open |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['threshold']:.2f} | {r['cases']} | {r['singletons']:.0%} | {r['median_size']} | "
                     f"{r['max_size']} | {r['cohesion']:.3f} | {r['would_open']} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--thresholds", default="0.2,0.25,0.3,0.35,0.4,0.5")
    args = parser.parse_args()
    for r in run([float(x) for x in args.thresholds.split(",")]):
        print(r)
