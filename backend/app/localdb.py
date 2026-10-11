"""Embedded local Postgres + pgvector for development and tests (pgserver).

Used only when SUPABASE_DB_URL is empty. Creates the Supabase roles the
migrations refer to, so the same SQL runs locally and on Supabase.
"""

import subprocess
import tempfile
from pathlib import Path

SUPABASE_ROLES = ("anon", "authenticated", "service_role")


def start(data_dir: Path) -> str:
    """Start (or reuse) the local server and return its connection URI."""
    import pgserver  # dev dependency

    data_dir.mkdir(parents=True, exist_ok=True)
    _recover_if_needed(data_dir)
    server = pgserver.get_server(str(data_dir), cleanup_mode=None)
    return server.get_uri()


def _recover_if_needed(data_dir: Path) -> None:
    """Finish crash recovery of a stopped, uncleanly shut down cluster first.

    pgserver writes the server log into the data directory with a 10 s start
    timeout. After a crash, recovery fsyncs every file of the data directory,
    and on Windows the open log file blocks that ("sharing violation"), so the
    start hangs. Recovering once with the log outside the data directory and
    stopping cleanly avoids it.
    """
    from pgserver._commands import POSTGRES_BIN_PATH

    if (data_dir / "postmaster.pid").exists() or not (data_dir / "PG_VERSION").exists():
        return
    out = subprocess.run([str(POSTGRES_BIN_PATH / "pg_controldata"), "-D", str(data_dir)],
                         capture_output=True, text=True)
    if "Database cluster state:               shut down" in out.stdout or out.returncode != 0:
        return
    log = Path(tempfile.gettempdir()) / f"pg-recover-{data_dir.name}.log"
    pg_ctl = str(POSTGRES_BIN_PATH / "pg_ctl")
    subprocess.run([pg_ctl, "-D", str(data_dir), "-o", "-h 127.0.0.1 -p 55999", "-l", str(log), "-w", "-t", "600",
                    "start"], check=True, capture_output=True)
    subprocess.run([pg_ctl, "-D", str(data_dir), "stop", "-m", "fast", "-t", "60"], check=True, capture_output=True)


def create_supabase_roles(conn) -> None:
    for role in SUPABASE_ROLES:
        conn.execute(
            f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') "
            f"then create role {role} nologin; end if; end $$;"
        )


def ensure_supabase_roles(conn) -> list[str]:
    """Create the Supabase roles that are missing, if the current user may.

    On Supabase they all exist (no-op). On a plain Postgres without CREATEROLE
    nothing is created; the migrations guard every role-specific statement.
    Returns the roles created.
    """
    existing = {r[0] if isinstance(r, tuple) else r["rolname"] for r in conn.execute(
        "select rolname from pg_roles where rolname = any(%s)", (list(SUPABASE_ROLES),)
    ).fetchall()}
    missing = [r for r in SUPABASE_ROLES if r not in existing]
    if not missing:
        return []
    row = conn.execute("select rolsuper or rolcreaterole as ok from pg_roles where rolname = current_user").fetchone()
    allowed = row[0] if isinstance(row, tuple) else row["ok"]
    if not allowed:
        return []
    create_supabase_roles(conn)
    return missing
