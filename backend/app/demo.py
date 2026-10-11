"""Demo readiness: python -m app.demo <command>

  build [--fresh]   migrate, seed, embed, group cases, score every config, build the
                    RAG index, create the demo users. --fresh wipes the LOCAL database
                    first (refused on Supabase).
  users             create the demo users (fixed ids, idempotent) and print them
  e2e "G60 / USA"   end to end through the HTTP API for one config: top open case ->
                    confirm -> requirement -> approve -> RAG answer with citations ->
                    audit chain report. Another config only needs another name.
  dump [file]       pg_dump of the current database (custom format) for offline demos
  restore <file>    restore a dump into the LOCAL database (e.g. when Supabase is paused)

Raw records live in backend/.data/raw-records (STORAGE_BACKEND=local) and are part
of the offline kit together with the dump.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import time
import uuid
from pathlib import Path

from app import audit, db
from app.config import BACKEND_DIR, get_settings

DUMP_DEFAULT = BACKEND_DIR / ".data" / "demo.dump"
REPORTS = BACKEND_DIR / "reports"
# Fixed ids so the UI can keep them in its .env; names are placeholders.
DEMO_USERS = [
    ("00000000-0000-4000-8000-000000000001", "Demo PM 5 Series", "pm", ["G60"]),
    ("00000000-0000-4000-8000-000000000002", "Demo PM 7 Series", "pm", ["G70"]),
    ("00000000-0000-4000-8000-000000000003", "Demo PM 1 Series", "pm", ["F70"]),
    ("00000000-0000-4000-8000-000000000004", "Demo engineer", "engineer", []),
    ("00000000-0000-4000-8000-000000000005", "Demo admin", "admin", []),
]


def _is_local() -> bool:
    return not get_settings().db_url


def _bin(name: str) -> str:
    from pgserver._commands import POSTGRES_BIN_PATH

    return str(POSTGRES_BIN_PATH / name)


def users() -> list[dict]:
    with db.transaction() as conn:
        for uid, name, role, vehicles in DEMO_USERS:
            audit.insert(conn, "app_user", {"id": uuid.UUID(uid), "name": name, "role": role, "vehicles": vehicles},
                         actor="seed:demo", rationale="demo user", on_conflict_do_nothing=True)
        return conn.execute("select id, name, role, vehicles from app_user order by name").fetchall()


def build(fresh: bool = False) -> dict:
    from app import migrate
    from app.cases import pipeline
    from app.rag import index
    from app.scoring import score
    from app.seed import run as seed
    from app.signals import embed

    timings: dict[str, float] = {}
    # Batch steps (seed, embeddings, RAG index) can exceed Supabase's 2 min default.
    get_settings().db_statement_timeout = get_settings().db_statement_timeout or "0"
    db.set_database_url(None)  # new pool with the setting

    def step(name, fn):
        t = time.time()
        out = fn()
        timings[name] = round(time.time() - t, 1)
        print(f"{name}: {out if not isinstance(out, dict) else json.dumps(out, default=str)[:300]}"
              f" ({timings[name]} s)")
        return out

    if fresh:
        if not _is_local():
            raise SystemExit("--fresh only wipes the local database; refused while SUPABASE_DB_URL is set")
        with db.connect(autocommit=True) as conn:
            conn.execute("drop schema public cascade")
            conn.execute("create schema public")
            conn.execute("grant all on schema public to public")
        db.set_database_url(None)
    step("migrate", lambda: migrate.apply_all(verbose=False))
    step("seed", lambda: seed.seed_all())

    def _embed():
        with db.transaction() as conn:
            return embed.backfill(conn)

    step("embed", _embed)
    step("cases", lambda: pipeline.run(initial=True))

    def _score():
        with db.transaction() as conn:
            return {c["name"]: score.score_config(conn, c["id"])
                    for c in conn.execute("select id, name from config where active order by name").fetchall()}

    step("score", _score)

    def _index():
        with db.transaction() as conn:
            return index.rebuild(conn)

    step("rag_index", _index)
    step("users", lambda: len(users()))
    return timings


def e2e(config_name: str, question: str | None = None) -> Path:
    """The demo path through the real API (in-process HTTP client, dev auth)."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.reports import audit_chain

    get_settings().auth_mode = "dev"
    with db.transaction() as conn:
        cfg = conn.execute("select * from config where name = %s", (config_name,)).fetchone()
        if cfg is None:
            raise SystemExit(f"no config {config_name!r}")
        profile = conn.execute("select id, name from weight_profile where config_id = %s and is_default",
                               (cfg["id"],)).fetchone()
        pm = conn.execute("select id from app_user where role = 'pm' and %s = any(vehicles) limit 1",
                          (cfg["vehicle"],)).fetchone() or conn.execute(
            "select id from app_user where role = 'admin' limit 1").fetchone()
    if pm is None:
        raise SystemExit("no demo users: run python -m app.demo users")
    client = TestClient(app, raise_server_exceptions=True)
    headers = {"X-User-Id": str(pm["id"])}
    log: list[str] = [f"# End-to-end run: {config_name}", "", f"Run at {dt.datetime.now():%Y-%m-%d %H:%M}, "
                      f"profile '{profile['name']}', PM `{pm['id']}`, all calls through the HTTP API.", ""]

    def call(method: str, url: str, **kw):
        r = client.request(method, url, headers=headers, **kw)
        log.append(f"- `{method} {url}` -> {r.status_code}")
        if r.status_code >= 400:
            raise SystemExit(f"{method} {url}: {r.status_code} {r.text[:500]}")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    board = call("GET", "/cases", params={"profile_id": str(profile["id"]), "state": ["open"]})
    if not board:
        raise SystemExit("no open case on the board")
    top = board[0]
    log += ["", f"Top open case: **{top['id']}** {top['title']} (score {top['score']:.3f}, "
            f"{top['distinct_customers']} customers, car area {top['car_area']}).", "", f"Rationale: {top['rationale']}",
            ""]
    call("GET", f"/cases/{top['id']}")
    confirmed = call("POST", f"/cases/{top['id']}/vehicles/{cfg['vehicle']}/transition",
                     json={"action": "confirm", "rationale": f"demo: confirmed for {config_name} after review"})
    rids = confirmed["requirements"]
    rid = next((r for r in rids if f"-{_market_code(cfg['market'])}-" in r), rids[0] if rids else None)
    if rid is None:
        raise SystemExit("no requirement generated")
    req = call("GET", f"/requirements/{rid}")
    call("POST", f"/requirements/{rid}/status", json={"status": "in_discussion", "rationale": "demo: with engineering"})
    call("POST", f"/requirements/{rid}/status", json={"status": "approved", "rationale": "demo: approved by the PM"})
    answer = call("POST", f"/chat/requirement/{rid}",
                  json={"question": question or f"What do customers say about {top['topic']}?"})
    export = call("GET", "/requirements/export", params={"profile_id": str(profile["id"])})
    log += ["", f"Requirement **{rid}**: {req['title']} (cost {req['cost_size']} from {req['cost_source']}, "
            f"release target {req['release_target']}, evidence {req['evidence_level']}).", "",
            f"RAG answer ({answer['prompt_version']}, {len(answer['cited_signals'])} cited of "
            f"{len(answer['retrieved_signals'])} retrieved signals):", "", "> " + answer["content"].replace("\n", "\n> "),
            "", f"Export: {len(export['items'])} approved requirement(s) in the top 10.", ""]
    with db.transaction() as conn:
        chain_md = audit_chain.render(audit_chain.collect(conn, rid))
    REPORTS.mkdir(exist_ok=True)
    slug = config_name.replace(" / ", "_").replace(" ", "_")
    out = REPORTS / f"e2e_{slug}.md"
    out.write_text("\n".join(log) + "\n\n" + chain_md.replace("# Audit chain", "## Audit chain", 1), encoding="utf-8")
    return out


def _market_code(market: str) -> str:
    return {"USA": "USA", "China": "CN", "Europe": "EU", "Rest of World": "RoW"}.get(market, market)


def dump(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_bin("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--file", str(path),
                    db.database_url()], check=True)
    return path


def restore(path: Path) -> None:
    if not _is_local():
        raise SystemExit("restore only into the local database; unset SUPABASE_DB_URL first")
    subprocess.run([_bin("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges",
                    "--dbname", db.database_url(), str(path)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--fresh", action="store_true")
    sub.add_parser("users")
    e = sub.add_parser("e2e")
    e.add_argument("config")
    e.add_argument("--question")
    d = sub.add_parser("dump")
    d.add_argument("file", nargs="?", default=str(DUMP_DEFAULT))
    r = sub.add_parser("restore")
    r.add_argument("file")
    args = parser.parse_args()
    if args.cmd == "build":
        print(build(args.fresh))
        for u in users():
            print(f"{u['id']}  {u['role']:<9} {u['name']}  {u['vehicles']}")
    elif args.cmd == "users":
        for u in users():
            print(f"{u['id']}  {u['role']:<9} {u['name']}  {u['vehicles']}")
    elif args.cmd == "e2e":
        print(e2e(args.config, args.question))
    elif args.cmd == "dump":
        print(dump(Path(args.file)))
    elif args.cmd == "restore":
        restore(Path(args.file))
        print("restored")


if __name__ == "__main__":
    main()
