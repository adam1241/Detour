# ADR-006: Database migrations

- **Status:** Accepted by Adam, 2026-11-18 (approved as part of the `feat/db-schema-seed` plan; recorded here after review feedback)
- **Spec refs:** §6.2, §9 item 5 (hooks block edits to applied migrations), CLAUDE.md ("Changing the DB schema or editing an applied migration")

## Context

The schema (spec §6.2) has to be created the same way in CI's PostGIS container, on Neon (ADR-002) and on a developer's database. Applied migrations must be immutable: the Claude Code hook and the CI `lint-test` check already reject edits to migrations that exist on `main`. The project has a 4-day timebox and one corridor's worth of tables.

## Options

| | Plain numbered SQL + small runner (`db/migrate.py`) | Alembic | Supabase/Neon CLI or dbmate/sqitch |
|---|---|---|---|
| New dependencies | None (uses `psycopg`, already in dev deps) | `alembic` + SQLAlchemy | A separate binary/tool in CI and locally |
| PostGIS DDL | Written directly in SQL | Mostly raw SQL anyway (`op.execute`) | Raw SQL |
| Fits "applied = immutable" checks | Yes: one file per migration in `db/migrations/` | Yes, but versions live in Python files | Yes |
| Down-migrations | No | Yes | Varies |
| Effort | ~70 lines, tested | Setup + learning | Setup per environment |

## Decision

**Plain numbered SQL files + a small runner.**
- Files are `db/migrations/NNNN_snake_name.sql`, applied in number order. Bad names and duplicate numbers are rejected.
- Each migration runs in its own committed transaction and is recorded in `schema_migrations`. Re-running applies nothing.
- The runner needs an idle autocommit connection, so an earlier migration can never be rolled back by a later failure.
- A Postgres advisory lock serialises concurrent runs against the same database.
- No down-migrations: fix forward with a new migration.

## Consequences

- No new dependency; the runner is covered by unit tests (file naming/ordering) and DB tests (apply, re-run, partial failure).
- No automatic rollback. A bad migration is fixed with a new one, which matches the "never edit an applied migration" rule.
- If the project outgrows this (many contributors, data migrations in Python), Alembic is the upgrade path; that would be a new ADR.
