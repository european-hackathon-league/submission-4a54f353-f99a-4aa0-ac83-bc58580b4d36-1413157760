"""Embedded local Postgres + pgvector for development and tests (pgserver).

Used only when SUPABASE_DB_URL is empty. Creates the Supabase roles the
migrations refer to, so the same SQL runs locally and on Supabase.
"""

from pathlib import Path

SUPABASE_ROLES = ("anon", "authenticated", "service_role")


def start(data_dir: Path) -> str:
    """Start (or reuse) the local server and return its connection URI."""
    import pgserver  # dev dependency

    data_dir.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(str(data_dir), cleanup_mode=None)
    return server.get_uri()


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
