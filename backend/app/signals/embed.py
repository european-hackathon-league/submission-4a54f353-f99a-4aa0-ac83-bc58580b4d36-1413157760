"""Backfill summary embeddings for valid signals that have none (seed signals):
python -m app.signals.embed

Each embedded signal gets ``extra.embedding_provider`` so vectors of different
providers are never mixed; switching provider re-embeds everything
(``--provider-change``).
"""

from __future__ import annotations

import argparse

from app import audit, db
from app.llm.embeddings import get_embedder
from app.signals.service import embedding_text

ACTOR = "agent:embedder"


def backfill(conn, batch: int = 500, all_rows: bool = False) -> int:
    embedder = get_embedder()
    # Out-of-scope signals (pricing) stay visible but are never grouped or searched by similarity.
    where = "validation = 'valid' and scope_tag = 'in_scope'" + ("" if all_rows else
                                      " and (embedding is null or coalesce(extra->>'embedding_provider', '') <> %s)")
    params = () if all_rows else (embedder.name,)
    rows = conn.execute(f"select id, summary, quote, extra from signal where {where} order by id", params).fetchall()
    done = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        vectors = embedder.embed([embedding_text(r) for r in chunk])
        updates = [({"id": r["id"]}, {"embedding": v, "extra": {**(r["extra"] or {}), "embedding_provider": embedder.name}})
                   for r, v in zip(chunk, vectors)]
        done += audit.update_many(conn, "signal", updates, actor=ACTOR,
                                  rationale=f"summary embedding ({embedder.name})")
    return done


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-change", action="store_true", help="re-embed every valid signal")
    args = parser.parse_args()
    with db.transaction() as conn:
        print(f"embedded {backfill(conn, all_rows=args.provider_change)} signals")
