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
# The people are invented (synthetic = true); they match the personas of the UI (web/src/mock/authored/people.ts).
# (id, name, role, lines, handle, pm_role, region, title, initials, color)
DEMO_USERS = [
    ("00000000-0000-4000-8000-000000000001", "Lena Hofmann", "pm", ["G60"], "u-lena", "vehicle_pm", "RoW",
     "Product Manager 5 Series · Rest of World", "LH", "#2563eb"),
    ("00000000-0000-4000-8000-000000000002", "Daniel Okafor", "pm", ["G70"], "u-daniel", "vehicle_pm", "global",
     "Product Manager 7 Series", "DO", "#0891b2"),
    ("00000000-0000-4000-8000-000000000003", "Sofia Rossi", "pm", ["F70"], "u-sofia", "vehicle_pm", "global",
     "Product Manager 1 Series", "SR", "#ea580c"),
    ("00000000-0000-4000-8000-000000000004", "Demo engineer", "engineer", [], "u-engineer", None, "global",
     "Engineering", "EN", "#64748b"),
    ("00000000-0000-4000-8000-000000000005", "Demo admin", "admin", [], "u-admin", None, "global",
     "Administrator", "AD", "#475569"),
    # The cross-series attribute PM works on every line (Baukasten).
    ("00000000-0000-4000-8000-000000000006", "Jonas Berger", "pm", [], "u-jonas", "attribute_pm", "global",
     "Product Manager Operating Concept & HMI · cross-series", "JB", "#7c3aed"),
    ("00000000-0000-4000-8000-000000000007", "Wei Zhang", "pm", ["G60"], "u-wei", "vehicle_pm", "CN",
     "Product Manager 5 Series · China", "WZ", "#dc2626"),
    # The head of product line signs off and resolves escalations (a PM of every line).
    ("00000000-0000-4000-8000-000000000008", "Katrin Maier", "pm", [], "u-katrin", "head", "global",
     "Head of Product Line · Compact to Luxury", "KM", "#0f766e"),
]


_U = "00000000-0000-4000-8000-00000000000"
DEMO_WORKSPACES = {_U + "1": "G60 / USA", _U + "2": "G70 / USA", _U + "3": "F70 / Europe", _U + "6": "G60 / Europe",
                   _U + "7": "G60 / China", _U + "8": "G60 / USA"}


def _is_local() -> bool:
    return not get_settings().db_url


def _bin(name: str) -> str:
    from pgserver._commands import POSTGRES_BIN_PATH

    return str(POSTGRES_BIN_PATH / name)


def users() -> list[dict]:
    """Create or refresh the demo users, then open the shared brief of each line
    (pre-filled from the data, unconfirmed) for its PM outside China."""
    from app.collab import briefs

    with db.transaction() as conn:
        for uid, name, role, vehicles, handle, pm_role, region, title, initials, color in DEMO_USERS:
            profile = {"name": name, "role": role, "vehicles": vehicles, "handle": handle, "pm_role": pm_role,
                       "region": region, "title": title, "initials": initials, "color": color, "synthetic": True}
            if conn.execute("select 1 from app_user where id = %s", (uid,)).fetchone():
                audit.update(conn, "app_user", {"id": uuid.UUID(uid)}, profile, actor="seed:demo",
                             rationale="demo user profile")
            else:
                audit.insert(conn, "app_user", {"id": uuid.UUID(uid), **profile}, actor="seed:demo",
                             rationale="demo user")
        # Where each demo PM starts: the market with the most evidence for their line (the feedback of
        # G60 and G70 is mostly from the USA), China for the China PM. They change it in the settings.
        for uid, config_name in DEMO_WORKSPACES.items():
            cfg = conn.execute("select c.id, (select id from weight_profile p where p.config_id = c.id order by"
                               " is_default desc, name limit 1) as profile from config c where c.name = %s and c.active",
                               (config_name,)).fetchone()
            if cfg and not conn.execute("select 1 from workspace where user_id = %s", (uid,)).fetchone():
                audit.insert(conn, "workspace", {"user_id": uuid.UUID(uid), "config_id": cfg["id"],
                                                 "weight_profile_id": cfg["profile"]},
                             actor="seed:demo", key="user_id", rationale=f"demo workspace {config_name}")
        for v in conn.execute("select code from vehicle where is_current order by code").fetchall():
            owner = conn.execute(
                "select id from app_user where pm_role = 'vehicle_pm' and region <> 'CN' and %s = any(vehicles)"
                " order by created_at limit 1", (v["code"],)).fetchone()
            if owner:
                briefs.ensure_shared(conn, v["code"], owner["id"])
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
    # Cases that existed before migration 0014 get their card texts and tags (a no-op on a fresh build).
    from app.cases import backfill

    step("case_cards", backfill.run)

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
