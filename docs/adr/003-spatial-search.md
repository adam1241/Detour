# ADR-003: Spatial search (PostGIS corridor search for v1, H3 as the scale path)

- **Status:** Accepted by Adam, 2026-11-17
- **Spec refs:** §5.3 (`find_stops_along_route`, `find_hotels_near`), §6.2, Non-goals (H3)

## Context

`find_stops_along_route` must return stops within `max_detour_km` of a route LineString, with `distance_from_route_km` and `position_along_route` (0–1). `find_hotels_near` searches around a point or route segment. v1 has ~50 points in one corridor; correctness and testability matter far more than speed. Spec already leans to PostGIS with `ST_DWithin` on geography.

Technical details that are easy to get wrong:
- `ST_DWithin` on **geography** uses metres; on **geometry (4326)** it uses degrees. Mixing them is the classic bug.
- `ST_LineLocatePoint` (for `position_along_route`) works on **geometry**, not geography.
- A GIST index on `geom geometry(Point,4326)` is **not** used by `ST_DWithin(geom::geography, ...)` unless there is a matching expression index.
- GeoJSON and PostGIS use **lon, lat** order; humans and many APIs use lat, lon.
- "Distance from route" is not "detour". A stop 10 km off the route adds ~20 km of driving (out and back).

## Options

### A. PostGIS corridor search (geography distance + geometry line-locate)
Store `geom geometry(Point, 4326)` with a GIST index **and** a GIST expression index on `(geom::geography)`. Query:
```sql
SELECT id, name, type,
       ST_Distance(s.geom::geography, r.line::geography) / 1000 AS distance_from_route_km,
       ST_LineLocatePoint(r.line, s.geom)                         AS position_along_route
FROM stops s, (SELECT ST_GeomFromGeoJSON($1) AS line) r
WHERE ST_DWithin(s.geom::geography, r.line::geography, $2 * 1000)
  AND s.type = ANY($3)
ORDER BY position_along_route
LIMIT $4;
```
- ➕ Exact, simple, standard. Easy to test with synthetic points at known distances.
- ➕ Works on Neon/Supabase and the `postgis/postgis` CI image.
- ➖ Cost grows with route length × candidate points; irrelevant at our size, matters at nationwide scale.

### B. Buffer-then-intersect (`ST_Buffer(route::geography, d)` then `ST_Intersects`)
- ➕ Can cache the buffer polygon per route.
- ➖ Buffering long lines is slower and approximate; no benefit at our size.

### C. H3 cells (pre-index points by H3 cell; cover the route with cells; join)
- ➕ Scales to millions of points; cache-friendly; good for "nationwide".
- ➖ Needs the `h3` / `h3-pg` extension (check host support), resolution tuning, and still needs an exact distance pass. Spec lists H3 as a non-goal.

### D. App-side search (load all points, compute with shapely/turf)
- ➕ No PostGIS needed.
- ➖ Throws away the spec's data model; doesn't show the spatial skill the project is meant to demonstrate.

## Decision

**Accepted: Option A for v1.** Default `max_detour_km` = 30 (spec §5.3). Document Option C as the scale path in this ADR (no code). Specifics:
- Column type `geometry(Point,4326)`; expression index `CREATE INDEX ... USING GIST ((geom::geography))`.
- `distance_from_route_km` is the perpendicular distance; also return `detour_km_estimate = 2 × distance` and document it as an estimate.
- `find_hotels_near` takes either a `[lon, lat]` point or a route segment as a GeoJSON LineString (the agent passes `get_route`'s geometry for that day). One function, one code path. *(Implemented 2026-11-18: a LineString instead of a `(from_position, to_position)` pair, since `get_route` already slices the route at any on-corridor point.)*
- Ordering by `position_along_route` is what enforces "no backtracking" upstream in `validate_plan`.

Tests (written first, run against the PostGIS service container):
- Point exactly on the line → distance ≈ 0, included.
- Points at known offsets (28 km in, 32 km out with `max_detour_km=30`) → in/out correctly. *(Implemented 2026-11-18, plus an exact boundary test: the cutoff is set to PostGIS's own WGS84 distance to a stop, which must be included, and 1 m less, which must exclude it. An on-route stop returns distance ≈ 0.)*
- Positions are monotonic for points placed in order along the line.
- Type filter and `limit` respected; empty result is `[]`, not an error.
- `EXPLAIN` test that the expression index is used. *(Implemented 2026-11-18 with `SET enable_seqscan = off`, which makes the check deterministic on a tiny test table: both queries must show `stops_geog_gix` / `hotels_geog_gix`. With real data volumes the planner decides on cost.)*
- `position_along_route` is returned unrounded, because `validate_plan` compares positions to detect backtracking.

## Consequences

- One migration with both indexes (schema approved as part of spec v0.2 §6.2).
- The H3 path stays a paragraph in the README "what's next", which is enough for the reviewer story.
- Detour distance is an estimate; the UI says "about N km off route", not exact added drive time.
