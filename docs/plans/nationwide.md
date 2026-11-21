# Plan: nationwide coverage

Status: **proposal**, waiting for Adam's decision on [ADR-008](../adr/008-nationwide-coverage.md).
Goal: plan a trip between any two US places, using real hotels and stops, without inventing
prices. Roughly 6 PRs, each under ~400 lines of code (data files excluded), in this order.

## What Adam needs to decide or do first
1. Accept, change or reject ADR-008 (data source, price policy, routing provider).
2. Approve the schema migration `0002` and the import dependency (`pyosmium` or the `osmium` tool).
3. Create two free keys and put them in `.env` (and later in the `evals` environment):
   `ORS_API_KEY` (openrouteservice.org) and `RIDB_API_KEY` (ridb.recreation.gov).

## PRs

| # | Branch | Scope | Tests first | Done when |
|---|---|---|---|---|
| 1 | `docs/nationwide-plan` | This plan, ADR-008 (proposed), spec ADR list; spec v0.7 scope change once accepted | — | Adam accepts ADR-008 |
| 2 | `feat/schema-nationwide` | Migration `0002`: nullable `price_usd`/`points_per_night`/`est_cost_usd`; `source` adds `osm`, `ridb`; `source_ref`, `licence` columns. Loader accepts the new sources; hand-sourced rows keep priority | Migration applies on PostGIS; loader rejects a price without `source_url`; corridor tests unchanged | `spatial` CI green; Neon migrated |
| 3 | `feat/import-osm-ridb` | `scripts/import_osm.py` (Geofabrik US extract → Choice-brand hotels by `brand:wikidata`, wineries, farms, breweries, museums) and `scripts/import_ridb.py` (campgrounds with RV sites). Output CSVs, then load | Filter unit tests on a small sample PBF/JSON; brand list test; no row without coordinates and `source_ref` | Counts per type and state reported in the PR; Adam spot-checks a sample |
| 4 | `feat/routing-provider` | `RouteProvider` interface: `CorridorCache` (today) and `OpenRouteService` (live), per-request cache, stdlib HTTP, retries. Nominatim geocoder with cache and 1 req/s. `validate_plan` and tools take the provider | Fake-transport tests like the OpenAI client; validate tests run on both providers with fixed routes | Corridor evals unchanged (20/20) |
| 5 | `feat/planner-nationwide` | Tools accept any US origin/destination; `find_stops_along_route` and `find_hotels_near` work on any route; cost and validate handle unpriced nights (`unpriced_nights` rule, partial totals); prompt v3 | Cost/validate tests for unpriced nights and partial budgets; planner loop tests with a fake model | New eval cases in 3 regions (e.g. Denver→Moab, Austin→Santa Fe, Portland→San Francisco) pass in a separate, non-gating job; corridor gate still 20/20 |
| 6 | `feat/ui-nationwide` | Free origin/destination, map fits any route, "price not available" badges, OSM/RIDB attribution, tooltips for the new controls | Server tests for the new request fields | Live plan for a non-corridor trip shown in the PR |

## Risks and cut-lines
- **Live services vary** (rate limits, outages). Cut-line: keep the corridor as the default demo,
  and nationwide behind a toggle.
- **Partial prices weaken the budget feature** outside sourced areas. Cut-line: hand-source
  prices for 3 demo regions (about 15 hotels each, the same way as the corridor) in PR 3.
- **OSM brand tags are incomplete** in places. The PR 3 report shows counts per state so gaps are
  visible.
- **Database size:** a few tens of thousands of rows fits Neon's free tier; check before loading.
- **Latency:** live routing adds a few hundred ms per call; the planner makes few route calls, so
  the 45 s target should hold. Measured in PR 5.
