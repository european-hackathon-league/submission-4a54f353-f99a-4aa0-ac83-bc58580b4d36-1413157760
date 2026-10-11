"""Signal write path: gate -> embed -> store (valid or quarantined), and the
PM actions on the quarantine. Agents call ``ingest``; they never write cases."""

from __future__ import annotations

import datetime as dt

from app import audit, taxonomy
from app.llm.embeddings import get_embedder
from app.signals.gate import check, load_context

CONTENT_COLUMNS = ("summary", "quote", "quote_en", "feature_hint", "embedding")


def embedding_text(row: dict) -> str:
    """LLM summaries are embedded alone. Label-based seed summaries only repeat the
    BMW labels (and their kind), so feature hint + quote are embedded instead;
    otherwise clustering would split likes and complaints about the same problem
    (changes.md 3.2)."""
    if (row.get("extra") or {}).get("kind_source") in ("bmw_label", "heuristic"):
        return f"{row.get('feature_hint') or ''}. {(row.get('quote') or '')[:400]}".strip(". ")
    return row.get("summary") or ""


def ingest(conn, proposals: list[dict], *, actor: str, run_id=None, prompt_version=None, model=None) -> dict:
    """Validate and store proposed signals. Returns counts and the stored rows."""
    ctx = load_context(conn, {p.get("record_id") for p in proposals if p.get("record_id")})
    rows, dropped = [], []
    for p in proposals:
        result = check(p, ctx, run_id=run_id, prompt_version=prompt_version, model=model)
        if result.row is None:
            dropped.append(result.reason)
        else:
            rows.append(result.row)
    valid = [r for r in rows if r["validation"] == "valid"]
    if valid:
        embedder = get_embedder()
        vectors = embedder.embed([embedding_text(r) for r in valid])
        for r, v in zip(valid, vectors):
            r["embedding"] = v
            r["extra"] = {**(r.get("extra") or {}), "embedding_provider": embedder.name}
    columns = sorted({k for r in rows for k in r})
    rows = [{c: r.get(c) for c in columns} for r in rows]
    stored = audit.insert_many_returning(conn, "signal", rows, actor=actor, run_id=run_id,
                                         rationale="signal through the validation gate")
    queue_taxonomy_review(conn, actor=actor, run_id=run_id)
    return {
        "valid": sum(1 for r in stored if r["validation"] == "valid"),
        "quarantined": sum(1 for r in stored if r["validation"] == "quarantined"),
        "dropped": len(dropped),
        "dropped_reasons": dropped,
        "rows": stored,
    }


def queue_taxonomy_review(conn, *, actor: str, run_id=None) -> int:
    """Put valid signals with topic ``other`` or a low-confidence topic into the
    review queue (once each). A human resolves them; no topic is created here."""
    rows = conn.execute(
        "select s.id, f.topic_id, s.extra->>'other_suggestion' as suggestion from signal s"
        " join feature f on f.id = s.feature_id"
        " where s.validation = 'valid' and (f.topic_id = %s or s.classification_confidence = 'low')"
        " and not exists (select 1 from taxonomy_proposal p where p.signal_id = s.id)",
        (taxonomy.OTHER,),
    ).fetchall()
    return audit.insert_many(conn, "taxonomy_proposal", [
        {"signal_id": r["id"], "suggested_name": r["suggestion"],
         "reason": "other" if r["topic_id"] == taxonomy.OTHER else "low_confidence"} for r in rows
    ], actor=actor, run_id=run_id, rationale="classifier could not place the signal with confidence")


def delete_quarantined(conn, signal_id: str, *, actor: str, rationale: str, request_id=None) -> dict:
    """PM deletes a quarantined signal: content purged, tombstone kept. The source
    record is purged too when no other signal uses it."""
    row = conn.execute("select id, record_id, validation from signal where id = %s", (signal_id,)).fetchone()
    if row is None:
        raise audit.NotFound(f"signal {signal_id} not found")
    if row["validation"] != "quarantined":
        raise ValueError("only quarantined signals can be deleted here")
    out = audit.tombstone(conn, "signal", {"id": signal_id}, CONTENT_COLUMNS, actor=actor, rationale=rationale,
                          extra_changes={"validation": "deleted"}, request_id=request_id)
    others = conn.execute(
        "select count(*) n from signal where record_id = %s and id <> %s and validation <> 'deleted'",
        (row["record_id"], signal_id),
    ).fetchone()["n"]
    if others == 0:
        audit.tombstone(conn, "source_record", {"record_id": row["record_id"]}, ("text_original", "text_en", "title"),
                        actor=actor, rationale=f"purged with quarantined signal {signal_id}: {rationale}",
                        request_id=request_id)
    return out


def release(conn, signal_id: str, changes: dict, *, actor: str, rationale: str, request_id=None) -> dict:
    """Re-check a quarantined signal after a correction (e.g. LLM classification
    arrived) and make it valid if it now passes the gate."""
    current = conn.execute("select * from signal where id = %s", (signal_id,)).fetchone()
    if current is None or current["validation"] != "quarantined":
        raise ValueError("signal is not quarantined")
    proposal = {k: current[k] for k in (
        "record_id", "kind", "severity", "summary", "quote", "quote_en", "aspect_group", "classified_by",
        "classification_confidence", "feature_hint", "evidence_role",
        "market", "market_basis", "vehicle", "chassis_code", "vehicle_role", "powertrain", "customer_ref", "reach",
        "reach_unit", "data_record_id", "extra") if current.get(k) is not None}
    if current.get("feature_id"):
        proposal["topic_id"] = conn.execute("select topic_id from feature where id = %s",
                                            (current["feature_id"],)).fetchone()["topic_id"]
    if current.get("competitor_id"):
        proposal["competitor_id"] = str(current["competitor_id"])
    proposal.update(changes)
    ctx = load_context(conn, {current["record_id"]})
    result = check(proposal, ctx)
    if not result.valid:
        raise ValueError(f"still fails the gate: {result.reason}")
    update = {k: result.row.get(k) for k in (
        "kind", "severity", "summary", "quote", "feature_id", "car_area", "aspect_group", "classified_by",
        "classification_confidence", "taxonomy_version", "market", "market_basis", "vehicle_role", "scope_tag",
        "extra")}
    update.update(validation="valid", reject_reason=None)
    update["embedding"] = get_embedder().embed([embedding_text(result.row)])[0]
    released = audit.update(conn, "signal", {"id": signal_id}, update, actor=actor, rationale=rationale,
                            action="release", request_id=request_id)
    queue_taxonomy_review(conn, actor=actor)
    return released


def resolve_proposal(conn, proposal_id: str, action: str, *, actor: str, rationale: str,
                     topic_id: str | None = None, aspect_group: str | None = None, request_id=None) -> dict:
    """PM resolves a taxonomy review row: map the signal to an existing topic (and
    optionally another aspect group), or dismiss it. A topic is never created here
    (``accepted_new_topic`` means a human changed ``taxonomy/`` and re-seeded).
    A mapped signal leaves its case so the next case run groups it under the new topic."""
    proposal = audit.fetch_for_update(conn, "taxonomy_proposal", {"id": proposal_id})
    if proposal["status"] != "open":
        raise ValueError("proposal already resolved")
    now = {"decided_by": actor, "decided_at": dt.datetime.now(dt.timezone.utc)}
    if action == "dismiss":
        return audit.update(conn, "taxonomy_proposal", {"id": proposal_id}, {"status": "dismissed", **now},
                            actor=actor, rationale=rationale, request_id=request_id)
    if action != "map":
        raise ValueError("action is map or dismiss")
    if not topic_id or topic_id == taxonomy.OTHER or topic_id not in taxonomy.topics():
        raise ValueError(f"unknown topic {topic_id!r}: pick one of the fixed taxonomy")
    if aspect_group and aspect_group not in taxonomy.aspect_groups():
        raise ValueError(f"unknown aspect group {aspect_group!r}")
    feature = conn.execute("select id from feature where topic_id = %s", (topic_id,)).fetchone()
    if feature is None:
        raise audit.NotFound(f"topic {topic_id} is not seeded")
    changes = {"feature_id": feature["id"], "car_area": [taxonomy.car_area_of(topic_id)], "classified_by": "pm",
               "classification_confidence": "high", "taxonomy_version": taxonomy.version(), "case_id": None}
    if aspect_group:
        changes["aspect_group"] = aspect_group
        if aspect_group == "price":
            changes["scope_tag"] = "out_of_scope"
    audit.update(conn, "signal", {"id": proposal["signal_id"]}, changes, actor=actor, rationale=rationale,
                 action="reclassify", request_id=request_id)
    return audit.update(conn, "taxonomy_proposal", {"id": proposal_id},
                        {"status": "mapped_to_existing", "resolved_feature_id": feature["id"], **now},
                        actor=actor, rationale=rationale, request_id=request_id)
