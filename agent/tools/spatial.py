"""Spatial search tools (spec §5.3, ADR-003): find_stops_along_route, find_hotels_near.

PostGIS does the work. Distances use geography (metres) on the geography expression indexes;
position along the route uses geometry (ST_LineLocatePoint). Money stays Decimal.
Bad input raises ToolInputError (a stable code the agent can act on) before any query runs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import psycopg

STOP_TYPES = ("winery", "farm", "brewery", "museum")
MAX_DETOUR_KM = 100.0
MAX_RADIUS_KM = 50.0
MAX_LIMIT = 50

Near = Sequence[float] | dict[str, Any]


class ToolInputError(ValueError):
    """Invalid tool arguments, in a form the agent can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message}


def _check_point(point: Sequence[float]) -> list[float]:
    if len(point) != 2 or not all(isinstance(v, int | float) for v in point):
        raise ToolInputError("bad_geometry", f"expected [lon, lat], got {list(point)!r}")
    lon, lat = float(point[0]), float(point[1])
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        raise ToolInputError("bad_geometry", f"[lon, lat] out of range: {[lon, lat]!r}")
    return [lon, lat]


def _check_line(geometry: dict[str, Any]) -> str:
    if geometry.get("type") != "LineString":
        raise ToolInputError("bad_geometry", "route must be a GeoJSON LineString")
    coords = geometry.get("coordinates") or []
    if len(coords) < 2:
        raise ToolInputError("bad_geometry", "route LineString needs at least 2 points")
    return json.dumps({"type": "LineString", "coordinates": [_check_point(c) for c in coords]})


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolInputError("bad_limit", f"limit must be between 1 and {MAX_LIMIT}")


STOPS_SQL = """
WITH r AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%(route)s), 4326) AS line)
SELECT s.id, s.name, s.type, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat, s.overnight_ok,
       s.est_cost_usd, s.description, s.source_url,
       ST_Distance(s.geom::geography, r.line::geography) / 1000 AS distance_km,
       ST_LineLocatePoint(r.line, s.geom) AS position
FROM stops s, r
WHERE ST_DWithin(s.geom::geography, r.line::geography, %(max_m)s)
  AND s.type = ANY(%(types)s)
ORDER BY position, s.id
LIMIT %(limit)s
"""


def find_stops_along_route(
    conn: psycopg.Connection,
    *,
    route: dict[str, Any],
    types: Sequence[str],
    max_detour_km: float = 30.0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Stops of the given types within max_detour_km of the route, in route order.

    Each result has distance_from_route_km (straight-line distance to the route),
    detour_km_estimate (2x that: there and back) and position_along_route (0 = start, 1 = end).
    """
    route_json = _check_line(route)
    if not types or any(t not in STOP_TYPES for t in types):
        raise ToolInputError("bad_type", f"types must be a non-empty subset of {list(STOP_TYPES)}")
    if not 0 < max_detour_km <= MAX_DETOUR_KM:
        raise ToolInputError("bad_max_detour", f"max_detour_km must be in (0, {MAX_DETOUR_KM}]")
    _check_limit(limit)

    rows = conn.execute(
        STOPS_SQL,
        {"route": route_json, "max_m": max_detour_km * 1000, "types": list(types), "limit": limit},
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "type": r[2],
            "lon": r[3],
            "lat": r[4],
            "overnight_ok": r[5],
            "est_cost_usd": r[6],
            "description": r[7],
            "source_url": r[8],
            "distance_from_route_km": round(r[9], 2),
            "detour_km_estimate": round(2 * r[9], 1),
            "position_along_route": round(r[10], 4),
        }
        for r in rows
    ]


HOTELS_SQL = """
WITH g AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%(near)s), 4326) AS geom)
SELECT h.id, h.name, h.brand_segment, ST_X(h.geom) AS lon, ST_Y(h.geom) AS lat, h.price_usd,
       h.points_per_night, h.amenities, h.source_url,
       ST_Distance(h.geom::geography, g.geom::geography) / 1000 AS distance_km
FROM hotels h, g
WHERE ST_DWithin(h.geom::geography, g.geom::geography, %(radius_m)s)
  AND (%(max_price)s::numeric IS NULL OR h.price_usd <= %(max_price)s::numeric)
ORDER BY distance_km, h.price_usd, h.id
LIMIT %(limit)s
"""


def find_hotels_near(
    conn: psycopg.Connection,
    *,
    near: Near,
    radius_km: float = 10.0,
    max_price_usd: Decimal | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Hotels within radius_km of a [lon, lat] point or a route segment (GeoJSON LineString),
    nearest first, optionally capped by nightly price."""
    if isinstance(near, dict):
        near_json = _check_line(near)
    else:
        near_json = json.dumps({"type": "Point", "coordinates": _check_point(near)})
    if not 0 < radius_km <= MAX_RADIUS_KM:
        raise ToolInputError("bad_radius", f"radius_km must be in (0, {MAX_RADIUS_KM}]")
    if max_price_usd is not None and not (max_price_usd.is_finite() and max_price_usd > 0):
        raise ToolInputError("bad_price", "max_price_usd must be a positive, finite amount")
    _check_limit(limit)

    rows = conn.execute(
        HOTELS_SQL,
        {
            "near": near_json,
            "radius_m": radius_km * 1000,
            "max_price": max_price_usd,
            "limit": limit,
        },
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "brand_segment": r[2],
            "lon": r[3],
            "lat": r[4],
            "price_usd": r[5],
            "points_per_night": r[6],
            "amenities": list(r[7]),
            "source_url": r[8],
            "distance_km": round(r[9], 2),
        }
        for r in rows
    ]
