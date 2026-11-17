# Day 1 plan: Foundation + data (spec §10, v0.2)

**Done when:** spatial tests pass in CI; first PRs merged through the pipeline.
**Limit:** at most 5 PRs; small setup steps merged together. Each PR < ~400 changed lines (seed CSVs, the cached GeoJSON and docs-only PRs flagged as exceptions).

Bootstrapping: the repo starts with its first commit on `chore/kickoff-docs`, so `main` never gets a direct commit. Branch protection (ADR-004) is turned on by Adam after PR 2's CI has run once, since GitHub can only require checks it has seen.

| # | Branch | Scope | Acceptance criteria | Tests | Approvals |
|---|---|---|---|---|---|
| 1 | `chore/kickoff-docs` | Spec v0.2, ADRs 001–005 accepted, README, `context-sources.md`, review checklist, this plan | All kickoff answers applied; ADRs Accepted | — (docs) | Adam merges |
| 2 | `chore/tooling-ci` | `uv` + ruff + mypy + pytest; pnpm workspace + eslint + prettier + tsc; `.pre-commit-config.yaml` with gitleaks; `.github/workflows/ci.yml` (lint, type-check, tests, PostGIS service container); `.claude/settings.json` hooks (block `.env*` and applied migrations, run tests after `agent/` edits); `.github/workflows/ai-review.yml` (advisory, uses `docs/review-checklist.md`); fill CLAUDE.md "Commands" | CI green; pre-commit blocks a planted fake key (shown in PR, not committed); hook blocks a `.env` edit (shown in PR); AI review comments on this PR | `test_smoke.py`; PostGIS job runs `SELECT postgis_version()` | New deps, CI workflows, AI review secret + spend limit (Adam) |
| 3 | `feat/db-schema-seed` | `db/migrations/0001_init.sql` per spec §6.2 (`hotels`, `stops`, `trips` with `vehicle`, `trace_json`, `source_url`; GIST + geography expression indexes; `CHECK (source = 'sample_public')`; `source_url NOT NULL`); migration runner; `load_seed.py`; 5-row fixture; then 15 hotels + 15 stops drafted only from fetched pages | Migration applies on empty DB in CI and on Neon; loader is idempotent; every row has `source_url` | Tests first: tables/indexes/constraints exist; re-run is a no-op; seed checks (unique IDs, `sample_public`, `source_url` present, point inside corridor bbox, price > 0, points ≥ 0, `overnight_ok` set) | Schema (approved in spec v0.2); Adam reviews every seed row |
| 4 | `feat/get-route` | `agent/tools/get_route.py` (OSRM client + sub-route from cache); `scripts/build_corridor.py` run once with waypoints DC → Front Royal → Luray → Staunton → Roanoke → Boone → Asheville; commit `db/seed/corridor_route.geojson` | Output matches §5.3 contract; sub-routes for on-corridor pairs; off-corridor → clear error; no network at runtime | Tests first against the cached file (no network in CI): shape, lon/lat order, units, sub-route shorter than full, legs sum to totals, error case | — |
| 5 | `feat/spatial-search` | `find_stops_along_route.py` and `find_hotels_near.py` per ADR-003 (`max_detour_km` default 30; point or route-segment mode) | Match §5.3 contracts; stops ordered by `position_along_route` | Tests first with synthetic points (29.9 / 30.1 km boundary, monotonic positions, type filter, limit, price filter, radius, segment mode, empty result) | — |

**Cut-lines if behind (in this order):**
1. AI review workflow moves out of PR 2 into a Day 2 morning PR (still before the agent PRs).
2. Sourced seed rows (15 + 15) land as a Day 2 PR if Adam's row review isn't done; PR 3 merges with the 5-row fixture.
3. `find_hotels_near` moves to Day 2 morning; PR 5 ships `find_stops_along_route` only (still meets "spatial tests pass in CI").
