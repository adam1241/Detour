"""Apply SQL migrations in db/migrations/ in order, once each.

Usage: DATABASE_URL=postgresql://... uv run python -m db.migrate
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Arbitrary constant: one advisory lock key per database serialises concurrent migrators.
MIGRATION_LOCK_KEY = 7_315_301


def migration_files(directory: Path) -> list[Path]:
    """Migration files sorted by number. Rejects bad names and duplicate numbers."""
    files = sorted(directory.glob("*.sql"))
    seen: dict[str, str] = {}
    for f in files:
        match = NAME_RE.match(f.name)
        if not match:
            raise ValueError(f"bad migration file name {f.name!r}: expected NNNN_snake_name.sql")
        number = match.group(1)
        if number in seen:
            raise ValueError(f"duplicate migration number {number}: {seen[number]}, {f.name}")
        seen[number] = f.name
    return files


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, each committed in its own transaction. Returns versions applied.

    Needs an idle autocommit connection: otherwise the per-migration transactions would be
    savepoints inside one outer transaction, and a later failure could roll back earlier
    migrations together with their ledger rows.
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection")
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise ValueError("apply_migrations needs an idle connection (no open transaction)")
    files = migration_files(directory)
    conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    try:
        return _apply_locked(conn, files)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def _apply_locked(conn: psycopg.Connection, files: list[Path]) -> list[str]:
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []
    for f in files:
        version = f.stem
        if version in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text(encoding="utf-8").encode())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        applied.append(version)
    return applied


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))
    with psycopg.connect(url, autocommit=True, connect_timeout=timeout) as conn:
        applied = apply_migrations(conn)
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
