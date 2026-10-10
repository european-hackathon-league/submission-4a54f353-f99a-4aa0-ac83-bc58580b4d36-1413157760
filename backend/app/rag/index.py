"""RAG index (rag_chunk): case and requirement pages, signals (summary + quote),
source texts, decisions and audit events about cases and requirements.

The index is derived data rebuilt from audited tables, so a rebuild writes one
audit event with the counts instead of one per chunk (changes.md 7.1).
"""

from __future__ import annotations


from app import audit, db
from app.llm.embeddings import get_embedder

SOURCE_TEXT_LIMIT = 1500


def _chunks(conn) -> list[dict]:
    out = []
    for c in conn.execute("select id, title, body_md from case_item").fetchall():
        vehicles = [r["vehicle"] for r in conn.execute("select vehicle from case_vehicle where case_id = %s",
                                                       (c["id"],)).fetchall()]
        out.append({"object_type": "case", "object_id": c["id"], "vehicle": vehicles[0] if len(vehicles) == 1 else None,
                    "market": None, "case_id": c["id"], "text": f"{c['id']}: {c['title']}\n{c['body_md']}"})
    for r in conn.execute("select id, case_id, vehicle, market, title, body_md from requirement"
                          " where status <> 'deleted'").fetchall():
        out.append({"object_type": "requirement", "object_id": r["id"], "vehicle": r["vehicle"], "market": r["market"],
                    "case_id": r["case_id"], "text": f"{r['id']}: {r['title']}\n{r['body_md']}"})
    for s in conn.execute(
            "select s.id, s.vehicle, s.market, s.case_id, s.kind, s.summary, s.quote, r.source_id, r.url"
            " from signal s join source_record r using (record_id) where s.validation = 'valid'").fetchall():
        out.append({"object_type": "signal", "object_id": str(s["id"]), "vehicle": s["vehicle"], "market": s["market"],
                    "case_id": s["case_id"],
                    "text": f"[{s['kind']}] {s['summary']}\nQuote: {s['quote']}\nSource: {s['source_id']}"})
    for r in conn.execute(
            "select distinct r.record_id, r.title, r.text_original, r.source_id, s.vehicle, s.case_id"
            " from source_record r join signal s using (record_id)"
            " where r.deleted_at is null and r.source_id <> 'bmw_feedback' and s.validation = 'valid'").fetchall():
        out.append({"object_type": "source_record", "object_id": r["record_id"], "vehicle": r["vehicle"],
                    "market": None, "case_id": r["case_id"],
                    "text": f"{r['title'] or r['record_id']}\n{(r['text_original'] or '')[:SOURCE_TEXT_LIMIT]}"})
    for d in conn.execute("select id, object_type, object_id, vehicle, actor, action, rationale from decision"
                          ).fetchall():
        case_id = d["object_id"] if d["object_type"] == "case" else None
        out.append({"object_type": "decision", "object_id": str(d["id"]), "vehicle": d["vehicle"], "market": None,
                    "case_id": case_id,
                    "text": f"Decision {d['action']} on {d['object_type']} {d['object_id']} by {d['actor']}: "
                            f"{d['rationale'] or ''}"})
    for a in conn.execute(
            "select seq, at, actor, action, object_type, object_id, rationale from audit_event"
            " where object_type in ('case', 'case_vehicle', 'requirement') and action <> 'create'"
            " and rationale is not null order by seq desc limit 5000").fetchall():
        case_id = a["object_id"].split("/")[0] if a["object_type"] in ("case", "case_vehicle") else None
        out.append({"object_type": "audit_event", "object_id": str(a["seq"]), "vehicle": None, "market": None,
                    "case_id": case_id,
                    "text": f"{a['at']:%Y-%m-%d} {a['actor']} {a['action']} {a['object_type']} {a['object_id']}: "
                            f"{a['rationale']}"})
    return out


def rebuild(conn, *, actor: str = "system") -> dict:
    chunks = _chunks(conn)
    embedder = get_embedder()
    vectors = embedder.embed([c["text"][:2000] for c in chunks]) if chunks else []
    conn.execute("delete from rag_chunk")
    for i in range(0, len(chunks), 500):
        batch = chunks[i : i + 500]
        with conn.cursor() as cur:
            cur.executemany(
                "insert into rag_chunk (object_type, object_id, vehicle, market, case_id, text, embedding)"
                " values (%s, %s, %s, %s, %s, %s, %s)",
                [(c["object_type"], c["object_id"], c["vehicle"], c["market"], c["case_id"], c["text"], v)
                 for c, v in zip(batch, vectors[i : i + 500])],
            )
    counts: dict[str, int] = {}
    for c in chunks:
        counts[c["object_type"]] = counts.get(c["object_type"], 0) + 1
    audit.write(conn, action="reindex", object_type="rag_chunk", object_id="all", actor=actor,
                after={"chunks": counts, "embedding_provider": embedder.name},
                rationale="RAG index rebuilt from audited tables")
    return counts


if __name__ == "__main__":
    with db.transaction() as conn:
        print(rebuild(conn))
