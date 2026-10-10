"""Apply SQL migrations in order: python -m app.migrate

Tracks applied files in schema_migrations with a checksum; a changed file that
was already applied is an error (write a new migration instead).
"""

import hashlib
import sys
from pathlib import Path

from app import db, localdb
from app.config import BACKEND_DIR, get_settings

MIGRATIONS_DIR = BACKEND_DIR / "supabase" / "migrations"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_all(url: str | None = None, verbose: bool = True) -> list[str]:
    applied_now: list[str] = []
    is_local = url is None and not get_settings().db_url
    with db.connect(url, autocommit=True) as conn:
        if is_local or (url and "127.0.0.1" in url):
            localdb.create_supabase_roles(conn)
        conn.execute(
            "create table if not exists schema_migrations ("
            " version text primary key, checksum text not null,"
            " applied_at timestamptz not null default now())"
        )
        done = {r["version"]: r["checksum"] for r in conn.execute("select * from schema_migrations")}
        for path in migration_files():
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(f"{path.name} changed after it was applied; add a new migration")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations (version, checksum) values (%s, %s)",
                    (path.name, checksum),
                )
            applied_now.append(path.name)
            if verbose:
                print(f"applied {path.name}")
    return applied_now


if __name__ == "__main__":
    names = apply_all()
    print(f"{len(names)} migration(s) applied" if names else "database is up to date")
    sys.exit(0)
