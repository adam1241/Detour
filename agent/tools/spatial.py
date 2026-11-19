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
# The full cached corridor is under 10k vertices per direction; anything far bigger is abuse.
MAX_ROUTE_POINTS = 20_000

Near = Sequence[float] | dict[str, Any]


class ToolInputError(ValueError):
    """Invalid tool arguments, in a form the agent can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message}


def _is_number(value: object) -> bool:
    """A real int or float (bool is an int subclass in Python, so exclude it)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _check_point(point: object) -> list[float]:
    if (
        not isinstance(point, list | tuple)
        or len(point) != 2
        or not all(_is_number(v) for v in point)
    ):
        raise ToolInputError("bad_geometry", f"expected [lon, lat] numbers, got {point!r}")
    lon, lat = float(point[0]), float(point[1])
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        raise ToolInputError("bad_geometry", f"[lon, lat] out of range: {[lon, lat]!r}")
    return [lon, lat]


def _check_line(geometry: object) -> str:
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise ToolInputError("bad_geometry", "route must be a GeoJSON LineString object")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        raise ToolInputError("bad_geometry", "route LineString needs a list of at least 2 points")
    if len(coords) > MAX_ROUTE_POINTS:
        raise ToolInputError(
            "bad_geometry", f"route has {len(coords)} points; the limit is {MAX_ROUTE_POINTS}"
        )
    return json.dumps({"type": "LineString", "coordinates": [_check_point(c) for c in coords]})


def _check_limit(limit: object) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ToolInputError("bad_limit", f"limit must be a whole number from 1 to {MAX_LIMIT}")


def _check_range(value: object, code: str, name: str, upper: float) -> float:
    if not _is_number(value) or not 0 < float(value) <= upper:  # type: ignore[arg-type]
        raise ToolInputError(code, f"{name} must be a number in (0, {upper}]")
    return float(value)  # type: ignore[arg-type]


def _check_price(value: object) -> Decimal | None:
    """Accept Decimal, int or float (JSON numbers); return an exact Decimal."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        price = value
    elif _is_number(value):
        price = Decimal(str(value))
    else:
        raise ToolInputError("bad_price", "max_price_usd must be a number")
    if not (price.is_finite() and price > 0):
        raise ToolInputError("bad_price", "max_price_usd must be a positive, finite amount")
    return price


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
    if not isinstance(types, list | tuple) or not types or any(t not in STOP_TYPES for t in types):
        raise ToolInputError("bad_type", f"types must be a non-empty list from {list(STOP_TYPES)}")
    max_detour_km = _check_range(max_detour_km, "bad_max_detour", "max_detour_km", MAX_DETOUR_KM)
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
    max_price_usd: Decimal | float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Hotels within radius_km of a [lon, lat] point or a route segment (GeoJSON LineString),
    nearest first, optionally capped by nightly price."""
    if isinstance(near, dict):
        near_json = _check_line(near)
    else:
        near_json = json.dumps({"type": "Point", "coordinates": _check_point(near)})
    radius_km = _check_range(radius_km, "bad_radius", "radius_km", MAX_RADIUS_KM)
    max_price_usd = _check_price(max_price_usd)
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
