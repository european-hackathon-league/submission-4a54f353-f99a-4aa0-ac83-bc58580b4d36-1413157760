"""Read fetched records again that were stored but never got their signals:
python -m app.fetch.reread [--sources a,b] [--yes]

A fetch run stores its records first and lets the agents read them after. When
the run stops in between (a crash, a failed batch), the records stay without
signals and are not fetched again. This reads them: records of fetch sources
that still have their text and no signal. Without ``--yes`` it only counts them.
"""

from __future__ import annotations

import argparse
import json

from app import db
from app.fetch import agents
from app.fetch.worker import INGEST_BATCH, ingest_extracted


def waiting(conn, only: list[str] | None) -> list[dict]:
    rows = conn.execute(
        "select r.* from source_record r join source_registry g on g.source_id = r.source_id"
        " where g.fetch_step is not null and r.text_original is not null"
        " and not exists (select 1 from signal s where s.record_id = r.record_id)"
        " order by r.source_id, r.record_id").fetchall()
    return [r for r in rows if not only or r["source_id"] in only]


def run(only: list[str] | None = None, apply: bool = False) -> dict:
    with db.transaction() as conn:
        records = waiting(conn, only)
    by_source: dict[str, int] = {}
    for r in records:
        by_source[r["source_id"]] = by_source.get(r["source_id"], 0) + 1
    result: dict = {"records waiting": by_source}
    if not apply or not records:
        return result
    with db.transaction() as conn:
        run_id = conn.execute("insert into agent_run (agent, mode, params) values ('fetch:reread', 'manual', %s)"
                              " returning id", (json.dumps({"sources": sorted(by_source)}),)).fetchone()["id"]
    # The agents read outside the database transaction; signals are stored in short transactions.
    extracted = agents.extract_many(records)
    totals: dict = {}
    for i in range(0, len(extracted), INGEST_BATCH):
        with db.transaction() as conn:
            for k, v in ingest_extracted(conn, extracted[i:i + INGEST_BATCH], run_id).items():
                totals[k] = totals.get(k, 0) + v
    with db.transaction() as conn:
        conn.execute("update agent_run set status = 'ok', ended_at = now(), records_fetched = %s,"
                     " signals_valid = %s, signals_quarantined = %s where id = %s",
                     (len(records), totals.get("valid", 0), totals.get("quarantined", 0), run_id))
    result["signals"] = totals
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    print(run([s for s in args.sources.split(",") if s] or None, apply=args.yes))
