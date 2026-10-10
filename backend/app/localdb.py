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
