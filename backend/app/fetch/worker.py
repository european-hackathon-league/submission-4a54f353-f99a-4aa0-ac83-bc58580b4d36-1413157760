"""Daily fetch loop: python -m app.fetch.worker [--backfill] [--steps 1-9]

For every enabled source of the requested fetch steps (source_registry), and
every subject (BMW lines first when they have open cases, then competitors):
fetch -> store raw response + records (audited) -> agents propose signals ->
validation gate -> feature / case linking -> figures -> rescoring ->
notifications. One agent_run per source. New evidence on a confirmed case only
raises its flag. ``--dry-run`` writes the source list and the expected request
volume (phase 8 checkpoint) without fetching.

Run daily with cron, e.g. ``0 3 * * * cd backend && uv run python -m app.fetch.worker``.
"""

from __future__ import annotations

import argparse
import json
import re
import traceback

from app import audit, db
from app.config import BACKEND_DIR, get_settings
from app.fetch import agents, subjects as subjects_mod
from app.fetch.adapters import api, downloads, feeds, libraries, scrape, sites  # noqa: F401  (registers adapters)
from app.fetch.adapters.base import ADAPTERS, FetchContext, FetchResult, NotConfigured
from app.fetch.http import BudgetExhausted, PoliteClient

PLAN_REPORT = BACKEND_DIR / "reports" / "fetch_plan.md"
# Sources fetched once per run for all subjects (feeds, newsrooms, market pages, app stores).
SHARED = feeds.SHARED | sites.SHARED | libraries.SHARED

# Rough requests per subject (incremental, backfill) for the dry-run estimate.
ESTIMATE = {
    "wikipedia": (1, 1), "wikidata": (1, 1), "nhtsa-vpic": (4, 4), "fueleconomy": (4, 4),
    "nhtsa-complaints": (12, 12), "nhtsa-recalls": (12, 12), "reddit": (16, 160), "youtube-comments": (20, 200),
    "tmc": (10, 2000), "genesis-owners": (10, 2000), "lucid-owners": (10, 400), "kbb": (2, 2),
    "motor-talk": (21, 4000), "pistonheads": (5, 1000), "carcomplaints": (10, 60),
    "ams": (1 + 10, 1 + 200), "autobild": (1 + 10, 1 + 200), "adac-autotest": (1 + 10, 1 + 200),
    "autoscout24": (1 + 10, 1 + 200), "youtube-transcripts": (20, 20),
}
SLUG_SOURCES = {"kbb", "motor-talk", "pistonheads", "carcomplaints"}


def parse_steps(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def _sources(conn, steps: list[int], only: list[str] | None) -> list[dict]:
    rows = conn.execute(
        "select r.*, (select max(ended_at) from agent_run a where a.agent = 'fetch:' || r.source_id"
        " and a.status in ('ok', 'partial')) as last_run_at"
        " from source_registry r where enabled and fetch_step = any(%s) order by fetch_step, source_id",
        (steps,)).fetchall()
    return [r for r in rows if not only or r["source_id"] in only]


def is_due(source: dict, now=None) -> bool:
    """A source runs when its last successful run is at least refresh_days old (cadence, changes.md 8.8)."""
    import datetime as dt

    if source.get("last_run_at") is None:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    # Half a day of slack so a daily cron at a slightly earlier time still runs.
    return now - source["last_run_at"] >= dt.timedelta(days=int(source.get("refresh_days") or 1), hours=-12)


def in_market(source: dict, subject) -> bool:
    """Country-specific sources only for subjects sold in that market (NHTSA, KBB, Motor-Talk, ...)."""
    from app.seed.loaders import country_to_market

    country = (source.get("country") or "").strip()
    if not country or not re.fullmatch(r"[A-Z]{2}", country) or not subject.markets:
        return True  # Global, per-country or unknown: no filter
    return country_to_market(country) in subject.markets


def _store(conn, result: FetchResult, actor: str, run_id) -> list[dict]:
    new = audit.insert_many_returning(conn, "source_record", result.records, actor=actor, key="record_id",
                                      run_id=run_id, on_conflict_do_nothing=True,
                                      rationale="fetched record (raw response in storage)")
    data = [d for d in result.data_records if d["value"] is not None or d["value_text"]]
    audit.insert_many(conn, "data_record", data, actor=actor, key="record_id", run_id=run_id,
                      on_conflict_do_nothing=True, rationale="fetched data record")
    return new


def run_agents(conn, records: list[dict], run_id) -> dict:
    from app.signals import service

    totals = {"valid": 0, "quarantined": 0, "dropped": 0}
    for record in records:
        proposals, model, prompt_version = agents.extract(record)
        if not proposals:
            continue
        out = service.ingest(conn, proposals, actor=agents.agent_for(record), run_id=run_id,
                             prompt_version=prompt_version, model=model)
        for k in totals:
            totals[k] += out[k]
    return totals


def fetch_source(conn_factory, client: PoliteClient, source: dict, subjects: list, backfill: bool,
                 with_agents: bool) -> dict:
    sid = source["source_id"]
    with conn_factory() as conn:
        run_id = conn.execute("insert into agent_run (agent, mode, params) values (%s, %s, %s) returning id",
                              (f"fetch:{sid}", "backfill" if backfill else "incremental",
                               json.dumps({"step": source["fetch_step"]}))).fetchone()["id"]
    ctx = FetchContext(client=client, backfill=backfill, run_id=run_id)
    cursor = dict(source["last_cursor"] or {})
    summary = {"source": sid, "records": 0, "signals": {}, "requests": 0, "notes": []}
    status, error = "ok", None
    try:
        results: list[FetchResult] = []
        if sid in SHARED:
            results.append(SHARED[sid](ctx, source, subjects, cursor))
        elif (source["access"] or "").startswith("Download"):
            results.append(downloads.fetch_downloads(ctx, source, subjects, cursor))
        elif sid in ADAPTERS:
            for s in [s for s in subjects if in_market(source, s)]:
                results.append(ADAPTERS[sid](ctx, source, s, cursor))
        else:
            raise NotConfigured(f"no adapter for {sid} (access {source['access']})")
        with conn_factory() as conn:
            new_records = []
            for r in results:
                new_records += _store(conn, r, f"fetch:{sid}", run_id)
                cursor.update(r.cursor)
                summary["requests"] += r.requests
                summary["notes"] += r.notes
            summary["records"] = len(new_records)
            if with_agents and new_records:
                summary["signals"] = run_agents(conn, new_records, run_id)
            changes = {"last_cursor": cursor}
            if backfill:
                import datetime as dt

                changes["backfill_done_at"] = dt.datetime.now(dt.timezone.utc)
            audit.update(conn, "source_registry", {"source_id": sid}, changes, actor="system", run_id=run_id,
                         rationale="fetch cursor after run")
    except NotConfigured as exc:
        status, error = "partial", str(exc)
        summary["notes"].append(error)
    except BudgetExhausted as exc:
        status, error = "partial", str(exc)
        summary["notes"].append(error)
    except Exception as exc:  # one failing source must not stop the loop
        status, error = "failed", f"{exc!r}\n{traceback.format_exc()[-1500:]}"
    with conn_factory() as conn:
        sig = summary["signals"] or {}
        conn.execute("update agent_run set status = %s, ended_at = now(), error = %s, records_fetched = %s,"
                     " signals_valid = %s, signals_quarantined = %s, params = params || %s where id = %s",
                     (status, error, summary["records"], sig.get("valid", 0), sig.get("quarantined", 0),
                      json.dumps({"requests": summary["requests"], "notes": summary["notes"][:20]}), run_id))
        if status == "failed":
            audit.insert(conn, "notification", {"type": "run_failed", "object_type": "agent_run",
                                                "object_id": str(run_id), "message": f"fetch {sid} failed: {exc_text(error)}"},
                         actor="system")
    summary["status"] = status
    return summary


def exc_text(error: str | None) -> str:
    return (error or "").split("\n", 1)[0][:200]


def dry_run(steps: list[int], vehicles: list[str] | None, only: list[str] | None) -> str:
    with db.transaction() as conn:
        sources = _sources(conn, steps, only)
        subjects = subjects_mod.load(conn, vehicles)
    lines = ["# Fetch plan (dry run)", "",
             f"Steps {steps[0]} to {steps[-1]}; {len(subjects)} subjects "
             f"({sum(1 for s in subjects if s.kind == 'vehicle')} BMW lines, "
             f"{sum(1 for s in subjects if s.kind == 'competitor')} competitors).", "",
             "| Step | Source | Access | Adapter | Every (days) | Requests per run | Per day (average) | Backfill | Needs |",
             "|---|---|---|---|---|---|---|---|---|"]
    tot_i = tot_b = 0
    tot_day = 0.0
    for s in sources:
        sid = s["source_id"]
        if sid in SHARED:
            inc, back = {"app-store": (17, 170), "google-play": (10, 100)}.get(
                sid, (1 + sites.MAX_ARTICLES, 1 + 200) if sid in sites.NEWSROOMS else (1, 1))
            adapter_name = SHARED[sid].__name__
        elif (s["access"] or "").startswith("Download"):
            inc = back = 0
            adapter_name = "download folder"
        elif sid in ADAPTERS:
            per = ESTIMATE.get(sid, (1, 1))
            slug_based = sid in scrape.MAGAZINES or sid in sites.MODEL_PAGES or sid in SLUG_SOURCES
            n = len([x for x in subjects if (not slug_based or x.slugs.get(sid)) and in_market(s, x)
                     and (sid not in scrape.FORUMS or x.label in scrape.FORUMS[sid])
                     and (not sid.startswith("youtube") or x.kind == "vehicle")])
            inc, back = per[0] * n, per[1] * n
            adapter_name = ADAPTERS[sid].__name__
        else:
            inc = back = 0
            adapter_name = "not built yet"
        settings = get_settings()
        needs = {"reddit": "" if settings.reddit_client_id else "Reddit OAuth app",
                 "youtube-comments": "" if settings.youtube_api_key else "YouTube Data API key",
                 "youtube-transcripts": "" if settings.youtube_api_key else "YouTube Data API key"}.get(sid, "")
        every = int(s.get("refresh_days") or 1)
        per_day = inc / every if not needs else 0.0
        tot_i += inc if not needs else 0
        tot_b += back
        tot_day += per_day
        lines.append(f"| {s['fetch_step']} | `{sid}` | {s['access']} | {adapter_name} | {every} | {inc} |"
                     f" {per_day:.0f} | {back} | {needs} |")
    lines += ["", f"Average: about {tot_day:.0f} requests per day (each source only when due, see 'Every'); "
              f"a day when every source is due would be about {tot_i}; the daily budget "
              f"FETCH_DAILY_REQUEST_BUDGET caps it either way, open cases first. One-time backfill: about {tot_b} "
              "requests, spread over days by the same budget (capped at 200 list pages per forum and subject). "
              "Sources that need a missing key are counted as 0.",
              "", "Politeness: robots.txt respected, at least 2 s between requests per host, retries with backoff, "
              "raw responses stored before parsing."]
    PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    PLAN_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(PLAN_REPORT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--steps", default="1-9")
    parser.add_argument("--sources", default="")
    parser.add_argument("--vehicles", default="")
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--no-agents", action="store_true")
    parser.add_argument("--no-cases", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="run every selected source, due or not")
    args = parser.parse_args()
    steps = parse_steps(args.steps)
    only = [s for s in args.sources.split(",") if s] or None
    vehicles = [v for v in args.vehicles.split(",") if v] or None
    if args.dry_run:
        print(dry_run(steps, vehicles, only))
        return
    with db.transaction() as conn:
        sources = _sources(conn, steps, only)
        subjects = subjects_mod.load(conn, vehicles)
    client = PoliteClient(budget=args.budget)
    for source in sources:
        if not (args.force or args.backfill or only or is_due(source)):
            print({"source": source["source_id"], "status": "skipped", "reason": "not due"})
            continue
        if source["delay_seconds"]:
            client.default_delay = max(client.default_delay, float(source["delay_seconds"]))
        print(fetch_source(db.transaction, client, source, subjects, args.backfill, not args.no_agents))
    if not args.no_cases:
        from app.cases import pipeline
        from app.scoring import score

        print(pipeline.run(initial=False))
        with db.transaction() as conn:
            for cfg in conn.execute("select id from config where active").fetchall():
                score.score_config(conn, cfg["id"])


if __name__ == "__main__":
    main()
