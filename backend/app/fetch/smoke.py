"""Live check of every fetch source: python -m app.fetch.smoke [--sources a,b] [--cap 8]

For each enabled source of fetch steps 1 to 9 the real adapter runs once, against
the real website or API, for one BMW line (and one competitor when the BMW line
returns nothing), with a small request cap. Nothing is written to a database:
the source list and the subjects are read from the LOCAL database, raw responses
go to a temporary folder, no agent and no LLM runs. Polite as the real loop
(robots.txt, delay per host, the project's user agent).

Writes backend/reports/fetch_smoke.md: per source the HTTP statuses, the number
of text and data records the adapter parsed and a sample.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import tempfile
import time
import traceback
from pathlib import Path

for _key in ("SUPABASE_DB_URL", "SUPABASE_URL", "OPENAI_API_KEY"):
    os.environ[_key] = ""
os.environ["STORAGE_BACKEND"] = "local"

from app import db  # noqa: E402
from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.fetch import subjects as subjects_mod  # noqa: E402
from app.fetch import worker  # noqa: E402
from app.fetch.adapters import api, downloads, libraries, scrape, sites  # noqa: E402
from app.fetch.adapters.base import ADAPTERS, FetchContext, FetchResult, NotConfigured  # noqa: E402
from app.fetch.http import BudgetExhausted, Disallowed, PoliteClient, Response  # noqa: E402

REPORT = BACKEND_DIR / "reports" / "fetch_smoke.md"


class RecordingClient(PoliteClient):
    """The polite client, remembering the status of every response."""

    def __init__(self, cap: int) -> None:
        super().__init__(budget=cap)
        self.statuses: list[int] = []

    def get(self, url: str, **kw) -> Response:
        r = super().get(url, **kw)
        self.statuses.append(r.status)
        return r


def _merge(results: list[FetchResult]) -> FetchResult:
    out = FetchResult()
    for r in results:
        out.records += r.records
        out.data_records += r.data_records
        out.notes += r.notes
    return out


def check(source: dict, subjects: list, cap: int) -> dict:
    sid = source["source_id"]
    client = RecordingClient(cap)
    ctx = FetchContext(client=client, backfill=False, run_id=None, max_pages=1)
    bmw = [s for s in subjects if s.kind == "vehicle"]
    rivals = [s for s in subjects if s.kind == "competitor"]
    out = {"source": sid, "step": source["fetch_step"], "access": source["access"], "status": "ok", "note": "",
           "subject": "", "result": FetchResult()}
    started = time.time()
    try:
        if sid in worker.SHARED:
            out["subject"] = "all lines"
            out["result"] = worker.SHARED[sid](ctx, source, bmw[:1] + rivals[:1], {})
        elif (source["access"] or "").startswith("Download"):
            out["subject"] = "download folder"
            out["result"] = downloads.fetch_downloads(ctx, source, bmw[:1], {})
        elif sid in ADAPTERS:
            slug_based = sid in scrape.MAGAZINES or sid in sites.MODEL_PAGES or sid in worker.SLUG_SOURCES
            fits = [s for s in bmw + rivals if worker.in_market(source, s) and (not slug_based or s.slugs.get(sid))
                    and (sid not in scrape.FORUMS or s.label in scrape.FORUMS[sid])]
            if not fits:
                out.update(status="no subject", note="no line or competitor has a slug or market for this source")
            tried = []
            for subject in fits[:2]:
                tried.append(subject.label)
                out["result"] = _merge([out["result"], ADAPTERS[sid](ctx, source, subject, {})])
                if out["result"].records or out["result"].data_records:
                    break
            out["subject"] = ", ".join(tried)
        else:
            out.update(status="no adapter", note=f"access {source['access']}")
    except NotConfigured as exc:
        out.update(status="not configured", note=str(exc))
    except BudgetExhausted:
        out.update(status="ok (cap reached)", note=f"stopped at the cap of {cap} requests; parsed results are lost")
    except Disallowed as exc:
        out.update(status="robots.txt forbids", note=str(exc)[:150])
    except Exception as exc:  # one failing source must not stop the check
        out.update(status="FAILED", note=f"{exc!r}"[:200] + " | " + traceback.format_exc().strip().splitlines()[-2][:120])
    out["http"] = client.statuses
    out["seconds"] = round(time.time() - started, 1)
    res = out["result"]
    bad = [s for s in client.statuses if s >= 400]
    if out["status"] == "ok" and bad and not (res.records or res.data_records):
        out["status"] = f"HTTP {bad[0]}"
    elif out["status"] == "ok" and not (res.records or res.data_records):
        out["status"] = "ok, nothing parsed"
    return out


def _sample(result: FetchResult) -> str:
    if result.records:
        r = result.records[0]
        text = " ".join((r.get("text_original") or "").split())[:140]
        return f"{(r.get('title') or '')[:50]} · {text}".strip(" ·")
    if result.data_records:
        d = result.data_records[0]
        return f"{d.get('metric')}: {d.get('value') if d.get('value') is not None else d.get('value_text')} {d.get('unit') or ''}"
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="")
    parser.add_argument("--cap", type=int, default=8, help="requests per source (robots.txt included)")
    parser.add_argument("--vehicle", default="G60")
    args = parser.parse_args()
    settings = get_settings()
    assert not settings.db_url, "the check must read the local database"
    settings.raw_local_dir = Path(tempfile.mkdtemp(prefix="fetch-smoke-"))
    only = [s for s in args.sources.split(",") if s] or None
    # A search costs 100 of the 10,000 daily YouTube quota units: two channels are enough for a check.
    api.youtube_channels = libraries.youtube_channels = lambda: ["UCcftUjNsimynCrKGFolLRKQ", "UCG72WbiCvdB6JKU-3YRP8Kg"]
    with db.transaction() as conn:
        sources = worker._sources(conn, list(range(1, 10)), only)
        disabled = conn.execute("select source_id, fetch_step, disabled_reason from source_registry"
                                " where not enabled and fetch_step between 1 and 9 order by fetch_step, source_id"
                                ).fetchall()
        subjects = subjects_mod.load(conn, [args.vehicle])
    rows = []
    for source in sources:
        row = check(source, subjects, args.cap)
        rows.append(row)
        r = row["result"]
        print(f"{row['source']:24} {row['status']:22} http={row['http']} text={len(r.records)} data={len(r.data_records)}"
              f" {row['seconds']}s {row['note'][:90]}", flush=True)
    lines = ["# Live check of the fetch sources", "",
             f"Run at {dt.datetime.now():%Y-%m-%d %H:%M}, vehicle {args.vehicle}, at most {args.cap} requests per source"
             " (robots.txt included). Real requests to the real sites; nothing written to a database.", "",
             "| Step | Source | Access | Result | HTTP | Text records | Data records | Tried for | Sample or note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        r = row["result"]
        statuses = ", ".join(f"{s}×{row['http'].count(s)}" for s in sorted(set(row["http"]))) or "none"
        sample = (_sample(r) or row["note"] or "; ".join(r.notes)[:140]).replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['step']} | `{row['source']}` | {row['access']} | {row['status']} | {statuses} |"
                     f" {len(r.records)} | {len(r.data_records)} | {row['subject']} | {sample} |")
    good = sum(1 for row in rows if row["result"].records or row["result"].data_records)
    lines += ["", f"{good} of {len(rows)} enabled sources returned parsed records."]
    if disabled and not only:
        lines += ["", "Switched off (not checked): " + ", ".join(
            f"`{d['source_id']}` ({d['disabled_reason'] or 'not in v1'})" for d in disabled) + "."]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{good} of {len(rows)} sources returned parsed records. Report: {REPORT}")


if __name__ == "__main__":
    main()
