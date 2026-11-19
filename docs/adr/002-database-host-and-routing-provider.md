# ADR-002: Database host and routing provider

- **Status:** Accepted by Adam, 2026-11-17
- **Spec refs:** §5.3 (`get_route`), §6, §7, §11 (rate limits), §12

## Context

Two external dependencies for Day 1:
1. A hosted **Postgres + PostGIS** for the seed data plus `trips`. Must work from AgentCore (AWS), Vercel, and CI.
2. A **routing provider** for `get_route` (LineString, distance, duration, legs).

Corridor fact that shapes the routing decision: the **fastest** DC → Asheville route is I-66/I-81/I-40, not Skyline Drive / Blue Ridge Parkway. If `get_route` returns the fastest route, seeded places along Skyline/BRP mostly fall outside the search distance. So the corridor needs **forced waypoints**. Some Parkway sections near Asheville have had extended closures since Hurricane Helene (2024); OSM data may not reflect current status. Live road conditions are out of scope (spec non-goals).

CI must never depend on a live routing API (flaky, rate-limited).

## Options: database

| | Supabase | Neon |
|---|---|---|
| PostGIS | Yes (extension) | Yes (extension) |
| Free tier | Yes; projects pause after a period of inactivity (verify) | Yes; scale-to-zero with a short cold start on first query (verify) |
| Branching | Paid feature | Free DB branches; could give each PR its own DB |
| Extras | Auth, storage, REST we don't need | Plain Postgres, nothing extra |
| Risk | Paused project during the demo | Cold-start latency on first plan |

CI uses the `postgis/postgis` Docker image as a service container either way, so the host only matters for preview/prod.

## Options: routing

| | OSRM public demo | Mapbox Directions | OpenRouteService | Cache the corridor once (via any of these) |
|---|---|---|---|---|
| Cost | Free | Free tier, then paid (verify limits) | Free key with daily quota | Free at runtime |
| Limits | Light use only, no SLA, car profile only | Needs token (secret) | Daily quota | None at runtime |
| Licence notes | OSM data (attribution) | Check ToS on caching results and non-Mapbox maps | OSM data | Depends on source |
| Fit for "stranger on live URL" | Poor at runtime | Good | OK | Good |

Options for storing a cached corridor: a `corridor_routes` table, or a GeoJSON file in the repo.

## Decision

- **Database: Neon** (Postgres + PostGIS). Warm it with a health check on page load to hide the cold start.
- **Routing: OSRM public server, called once** by `get_route` with fixed **town** waypoints:
  `Washington, DC → Front Royal, VA → Luray, VA → Staunton, VA → Roanoke, VA → Boone, NC → Asheville, NC`.
  The result is cached as **`db/seed/corridor_route.geojson`** (LineString + legs with distance and duration). **No new table.**
- At runtime, `get_route` serves any origin/destination on the corridor by cutting the cached line between the two points (`ST_LineSubstring` or the same in Python with shapely), with distance and duration prorated from the legs. Off-corridor requests return a clear "outside v1 coverage" error.
- `max_detour_km` default = **30** (spec §5.3).

### Waypoint check
- Front Royal is the north entrance of Skyline Drive; Luray sits next to it (Thornton Gap). Staunton is a few miles west of the Drive's south end (Rockfish Gap / Waynesboro). Roanoke is on the Parkway. Boone is a few miles from the Parkway (Blowing Rock area). Asheville is the destination and a Parkway town. They make sense.
- Caveat: OSRM routes **between** these towns mostly use parallel roads (US-340, I-81, US-221/421, I-40), not Skyline Drive or the Parkway themselves. With a 30 km search distance, places along both scenic roads are still in range. Drive times will be those of the town-to-town roads, not the slower 35–45 mph scenic roads. The README says so.
- Total distance and duration are measured by `get_route` when the cache is built, not assumed here.

## Consequences

- No routing calls at runtime: `get_route` is deterministic and fast, which helps the latency targets and makes evals reproducible.
- The cache-building script is run by hand and its output committed; re-running it is a reviewed PR.
- RV drive times may be optimistic (car profile). Tracked as a known limitation; an RV multiplier can be added later via ADR.
- If Neon's cold start shows up in the < 3s first-progress target, send the first progress event before the first DB query.

## Implementation note (2026-11-18)

Built by `scripts/build_corridor.py` (run by hand; output committed; written atomically). It geocodes the seven towns, then routes the six legs **in both directions** with OSRM, because one-way roads and turn restrictions can make them differ. Measured (car profile, no traffic, so real drive times will usually be longer):

| Direction | Distance | Duration |
|---|---|---|
| South: DC → Asheville | 797.2 km | 637.8 min |
| North: Asheville → DC | 796.3 km | 637.0 min |

`get_route` serves any two places on the corridor: town names (with aliases) or `[lon, lat]` points within 30 km of the route (the default `max_detour_km`), such as an overnight hotel. Points snap to the nearest route vertex; partial legs get OSRM's leg distance and duration prorated by geometry. Each direction uses its own cached legs. Errors carry a stable code (`outside_coverage`, `same_place`, `bad_waypoint`, `bad_place`) and the list of supported towns, so the agent can recover.

## Amendment 2026-11-18: geocoding provider

**Context.** The corridor waypoints (and later the seed rows' addresses) need coordinates. This is a one-off, low-volume need at build time, never at runtime.

**Options.**
- **OpenStreetMap Nominatim (public):** free, no key, same OSM data as OSRM. Usage policy: at most 1 request/second, identifying User-Agent, no bulk use.
- **Mapbox or Google geocoding:** accurate, but needs an account, an API key (a secret) and has licence terms on storing results.
- **Coordinates typed by hand:** no service, but not traceable to a source, which breaks the "no invented data" rule.

**Decision.** Nominatim, at build time only, 1 request/second, generic User-Agent, with the request URL stored next to each result as its source. Approved by Adam as part of the seed-sourcing defaults on 2026-11-18 (point 3).

**Consequences.** No new secret or account. Results are traceable (`source_url` per town). Attribution "© OpenStreetMap contributors (ODbL)" is shown in the README and, later, on the map.
