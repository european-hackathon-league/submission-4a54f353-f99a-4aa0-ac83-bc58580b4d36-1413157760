"""Group signals into cases: one case per topic and aspect group.

Initial run: signals are grouped by topic + aspect group, across all BMW lines
(a case can span lines). The aspect groups are neutral, so likes and complaints
about the same aspect land in one case. Only a large group (``split_min``
signals or more) is split further, by average-linkage agglomerative clustering
on the signal embeddings (cosine). Grouping never crosses a topic boundary.
Incremental run: a new signal joins the case of its topic + aspect group; if
that group was split into several cases it joins the most similar one when the
similarity to the case centroid reaches the threshold, else it opens a new
observed case. Case IDs never change; merges and splits go to case_lineage.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from app import audit
from app.cases import writer
from app.llm.embeddings import as_matrix

ACTOR = "agent:cases"
CLUSTER_KINDS = ("complaint", "wish", "like", "competitor_fact", "trend", "innovation")
SPLIT_MIN = 50


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def cluster_labels(vectors: np.ndarray, min_similarity: float) -> np.ndarray:
    if len(vectors) == 1:
        return np.zeros(1, dtype=int)
    model = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                    distance_threshold=1.0 - min_similarity)
    return model.fit_predict(vectors)


def group_labels(vectors: np.ndarray, min_similarity: float, split_min: int = SPLIT_MIN) -> np.ndarray:
    """One case for a topic + aspect group; similarity split only inside large groups."""
    if len(vectors) < split_min:
        return np.zeros(len(vectors), dtype=int)
    return cluster_labels(vectors, min_similarity)


def _label(feature: str, aspect_group: str | None) -> str:
    return f"{feature} ({aspect_group})" if aspect_group else feature


def _signals_for(conn, feature_id=None, unassigned_only=True) -> list[dict]:
    # The fallback topic "other" waits in the taxonomy review queue and is never clustered.
    where = ["s.validation = 'valid'", "s.scope_tag = 'in_scope'", "s.feature_id is not null",
             "s.embedding is not null", "f.topic_id is distinct from 'other'"]
    params: list = []
    if unassigned_only:
        where.append("s.case_id is null")
    if feature_id:
        where.append("s.feature_id = %s")
        params.append(feature_id)
    return conn.execute(
        "select s.id, s.feature_id, s.kind, s.severity, s.market, s.vehicle, s.customer_ref, s.evidence_role,"
        " s.quote, s.embedding, s.aspect_group, f.name as feature"
        " from signal s join feature f on f.id = s.feature_id"
        f" where {' and '.join(where)} order by s.feature_id, s.aspect_group, s.id",
        params,
    ).fetchall()


def create_case(conn, feature_id, feature_name: str, members: list[dict], *, aspect_group: str | None = None,
                run_id=None, rationale: str = "new cluster of signals") -> dict:
    text, model, prompt_version = writer.write(_label(feature_name, aspect_group), members)
    centroid = _unit(np.mean(as_matrix([m["embedding"] for m in members]), axis=0))
    case = audit.insert(conn, "case_item", {
        "feature_id": feature_id, "aspect_group": aspect_group, "title": text.title, "body_md": text.body_md,
        "embedding": centroid,
        "created_by": ACTOR, "tags": [],
    }, actor=ACTOR, run_id=run_id, rationale=f"{rationale} ({prompt_version}{', ' + model if model else ''})")
    audit.insert(conn, "page_version", {"object_type": "case", "object_id": case["id"], "version": 1,
                                        "body_md": text.body_md, "author": ACTOR},
                 actor=ACTOR, key=("object_type", "object_id", "version"), run_id=run_id)
    assign(conn, case["id"], members, run_id=run_id, rationale=rationale)
    return case


def assign(conn, case_id: str, members: list[dict], *, run_id=None, rationale: str) -> None:
    audit.update_many(conn, "signal", [({"id": m["id"]}, {"case_id": case_id}) for m in members],
                      actor=ACTOR, run_id=run_id, rationale=f"assigned to {case_id}: {rationale}")
    vehicles = sorted({m["vehicle"] for m in members if m["vehicle"]})
    existing = {r["vehicle"] for r in conn.execute(
        "select vehicle from case_vehicle where case_id = %s", (case_id,)).fetchall()}
    new_rows = [{"case_id": case_id, "vehicle": v, "state": "observed", "opened_by": None}
                for v in vehicles if v not in existing]
    if new_rows:
        audit.insert_many(conn, "case_vehicle", new_rows, actor=ACTOR, key=("case_id", "vehicle"), run_id=run_id,
                          rationale="case applies to this BMW line (signals from it)")
    # New evidence on a confirmed case only raises the flag; the case never changes by itself.
    for v in vehicles:
        cv = conn.execute("select state, flag_new_evidence from case_vehicle where case_id = %s and vehicle = %s",
                          (case_id, v)).fetchone()
        if cv and cv["state"] == "confirmed":
            n = sum(1 for m in members if m["vehicle"] == v)
            audit.update(conn, "case_vehicle", {"case_id": case_id, "vehicle": v},
                         {"flag_new_evidence": cv["flag_new_evidence"] + n}, actor=ACTOR, run_id=run_id,
                         rationale=f"+{n} signals since confirmation")
            audit.insert(conn, "notification", {
                "type": "new_evidence", "object_type": "case", "object_id": case_id, "vehicle": v,
                "message": f"{case_id}: +{n} new signals since confirmation",
            }, actor=ACTOR, run_id=run_id)


def initial(conn, min_similarity: float, *, split_min: int = SPLIT_MIN, run_id=None) -> dict:
    """Group all unassigned signals by topic + aspect group; split only large groups."""
    rows = _signals_for(conn)
    by_group: dict = defaultdict(list)
    for r in rows:
        by_group[(r["feature_id"], r["feature"], r["aspect_group"])].append(r)
    created = split_groups = 0
    planned = []
    for (feature_id, feature_name, aspect), members in by_group.items():
        vectors = as_matrix([m["embedding"] for m in members])
        labels = group_labels(vectors, min_similarity, split_min)
        groups: dict[int, list[dict]] = defaultdict(list)
        for m, label in zip(members, labels):
            groups[int(label)].append(m)
        why = f"topic + aspect group {_label(feature_name, aspect)}"
        if len(members) >= split_min:
            split_groups += 1
            why += f", large group split by cosine >= {min_similarity:.2f}"
        for group in sorted(groups.values(), key=len, reverse=True):
            planned.append((feature_id, feature_name, aspect, group, why))
    # Case pages are written in parallel first (cached); creating the cases then reads the cache.
    writer.prefetch([(_label(name, aspect), group) for _, name, aspect, group, _ in planned])
    for feature_id, feature_name, aspect, group, why in planned:
        create_case(conn, feature_id, feature_name, group, aspect_group=aspect, run_id=run_id, rationale=why)
        created += 1
    return {"signals": len(rows), "groups": len(by_group), "split_groups": split_groups, "cases": created}


def incremental(conn, min_similarity: float, *, run_id=None) -> dict:
    """Attach new signals to existing cases (or open new observed cases)."""
    rows = _signals_for(conn)
    attached = created = 0
    for r in rows:
        # Cases of the same topic + aspect group that still hold signals (a merged case holds none).
        cases = conn.execute(
            "select c.id, 1 - (c.embedding <=> %s) as sim from case_item c where c.feature_id = %s"
            " and c.aspect_group is not distinct from %s and c.embedding is not null"
            " and exists (select 1 from signal s where s.case_id = c.id)"
            " order by c.embedding <=> %s limit 2",
            (r["embedding"], r["feature_id"], r["aspect_group"], r["embedding"]),
        ).fetchall()
        if len(cases) == 1:
            assign(conn, cases[0]["id"], [r], run_id=run_id, rationale="case of the same topic + aspect group")
            attached += 1
        elif cases and cases[0]["sim"] >= min_similarity:
            assign(conn, cases[0]["id"], [r], run_id=run_id,
                   rationale=f"nearest case of the topic + aspect group, cosine {cases[0]['sim']:.2f}")
            attached += 1
        else:
            create_case(conn, r["feature_id"], r["feature"], [r], aspect_group=r["aspect_group"], run_id=run_id,
                        rationale="no case for this topic + aspect group" if not cases
                        else "no case above the similarity threshold")
            created += 1
    return {"attached": attached, "created": created}


def refresh_centroid(conn, case_id: str, *, run_id=None) -> None:
    rows = conn.execute("select embedding from signal where case_id = %s and embedding is not null",
                        (case_id,)).fetchall()
    if rows:
        centroid = _unit(np.mean(as_matrix([r["embedding"] for r in rows]), axis=0))
        audit.update(conn, "case_item", {"id": case_id}, {"embedding": centroid}, actor=ACTOR, run_id=run_id,
                     rationale="centroid after membership change")


def merge(conn, source_id: str, target_id: str, *, actor: str, rationale: str) -> None:
    """Move every signal of source into target. The source case stays (rejected,
    pointing to the target) so its ID and history remain resolvable."""
    members = conn.execute("select id, vehicle from signal where case_id = %s", (source_id,)).fetchall()
    audit.update_many(conn, "signal", [({"id": m["id"]}, {"case_id": target_id}) for m in members], actor=actor,
                      rationale=f"merge {source_id} -> {target_id}: {rationale}")
    audit.insert(conn, "case_lineage", {"from_case": source_id, "to_case": target_id, "op": "merge", "actor": actor,
                                        "rationale": rationale}, actor=actor)
    for cv in conn.execute("select vehicle from case_vehicle where case_id = %s", (source_id,)).fetchall():
        audit.update(conn, "case_vehicle", {"case_id": source_id, "vehicle": cv["vehicle"]},
                     {"state": "rejected", "state_reason": f"merged into {target_id}"}, actor=actor,
                     rationale=rationale, action="state_change")
    assign(conn, target_id, [dict(m) for m in members], rationale=f"merged from {source_id}")
    refresh_centroid(conn, target_id)


def split(conn, case_id: str, signal_ids: list[str], *, actor: str, rationale: str) -> dict:
    members = conn.execute(
        "select s.id, s.feature_id, s.kind, s.severity, s.market, s.vehicle, s.customer_ref, s.evidence_role,"
        " s.quote, s.embedding, s.aspect_group, f.name as feature"
        " from signal s join feature f on f.id = s.feature_id"
        " where s.case_id = %s and s.id = any(%s::uuid[])", (case_id, signal_ids)).fetchall()
    if not members:
        raise ValueError("no signals of this case selected")
    aspect = conn.execute("select aspect_group from case_item where id = %s", (case_id,)).fetchone()["aspect_group"]
    new = create_case(conn, members[0]["feature_id"], members[0]["feature"], list(members), aspect_group=aspect,
                      rationale=f"split from {case_id}: {rationale}")
    audit.insert(conn, "case_lineage", {"from_case": case_id, "to_case": new["id"], "op": "split", "actor": actor,
                                        "rationale": rationale}, actor=actor)
    refresh_centroid(conn, case_id)
    return new
