"""get_route tool (spec §5.3, ADR-002).

Serves routes on the v1 corridor from the cached OSRM result in
db/seed/corridor_route.geojson (built once by scripts/build_corridor.py). No network calls.

- Endpoints and waypoints are corridor town names (with aliases) or [lon, lat] points within
  SNAP_KM of the route, e.g. an overnight hotel. Points snap to the nearest route vertex.
- Each direction uses its own cached OSRM legs (south = DC to Asheville, north = reverse).
- Partial legs get OSRM's leg distance and duration prorated by the share of the leg's
  geometry they cover.
"""

from __future__ import annotations

import itertools
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

CORRIDOR_FILE = Path(__file__).resolve().parents[2] / "db" / "seed" / "corridor_route.geojson"
SNAP_KM = 30.0  # same as the default max_detour_km (spec §5.3)

Place = str | Sequence[float]

STATE_WORDS = {"VA": ("va", "virginia"), "NC": ("nc", "north carolina")}


class RouteError(ValueError):
    """A request the v1 corridor can't answer, in a form the agent can act on."""

    def __init__(self, code: str, message: str, supported_towns: Sequence[str]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.supported_towns = list(supported_towns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "supported_towns": self.supported_towns,
        }


@dataclass(frozen=True)
class _Track:
    """One direction of the corridor as a single polyline with cumulative totals."""

    towns: tuple[str, ...]
    coords: tuple[tuple[float, float], ...]
    cum_km: tuple[float, ...]
    cum_min: tuple[float, ...]
    boundaries: tuple[int, ...]  # vertex index where each town sits, in travel order


@dataclass(frozen=True)
class _Corridor:
    south: _Track
    north: _Track
    points: dict[str, list[float]]
    aliases: dict[str, str]


def _haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _aliases_for(town: str) -> set[str]:
    city, _, state = town.partition(", ")
    if state == "DC":
        return {"washington dc", "washington", "dc", "washington d c", "district of columbia"}
    base = _normalize(city)
    return {base} | {f"{base} {_normalize(word)}" for word in STATE_WORDS[state]}


def _build_track(features: list[dict[str, Any]]) -> _Track:
    towns = [features[0]["properties"]["from"]]
    coords: list[tuple[float, float]] = []
    cum_km: list[float] = []
    cum_min: list[float] = []
    boundaries = [0]
    for f in features:
        leg = [(float(x), float(y)) for x, y in f["geometry"]["coordinates"]]
        seg = [_haversine_km(p, q) for p, q in itertools.pairwise(leg)]
        geom_km = sum(seg) or 1.0
        km_scale = float(f["properties"]["distance_km"]) / geom_km
        min_scale = float(f["properties"]["duration_min"]) / geom_km
        if not coords:
            coords.append(leg[0])
            cum_km.append(0.0)
            cum_min.append(0.0)
        for point, length in zip(leg[1:], seg, strict=True):
            coords.append(point)
            cum_km.append(cum_km[-1] + length * km_scale)
            cum_min.append(cum_min[-1] + length * min_scale)
        boundaries.append(len(coords) - 1)
        towns.append(f["properties"]["to"])
    return _Track(
        towns=tuple(towns),
        coords=tuple(coords),
        cum_km=tuple(cum_km),
        cum_min=tuple(cum_min),
        boundaries=tuple(boundaries),
    )


@cache
def _corridor() -> _Corridor:
    data: dict[str, Any] = json.loads(CORRIDOR_FILE.read_text(encoding="utf-8"))
    by_direction: dict[str, list[dict[str, Any]]] = {"south": [], "north": []}
    for f in data["features"]:
        by_direction[f["properties"]["direction"]].append(f)
    south, north = _build_track(by_direction["south"]), _build_track(by_direction["north"])
    points = {name: [t["lon"], t["lat"]] for name, t in data["properties"]["towns"].items()}
    aliases = {alias: town for town in south.towns for alias in _aliases_for(town)}
    return _Corridor(south=south, north=north, points=points, aliases=aliases)


def _error(code: str, message: str) -> RouteError:
    return RouteError(code, message, _corridor().south.towns)


def _resolve_town(place: str) -> str:
    town = _corridor().aliases.get(_normalize(place))
    if town is None:
        raise _error(
            "outside_coverage",
            f"{place!r} is outside v1 coverage. Use a corridor town or a point on the route.",
        )
    return town


def _vertex(track: _Track, place: Place) -> int:
    """Index of the route vertex for a town name or an on-corridor [lon, lat] point."""
    if isinstance(place, str):
        town = _resolve_town(place)
        return track.boundaries[track.towns.index(town)]
    if len(place) != 2:
        raise _error("bad_place", f"expected [lon, lat], got {list(place)!r}")
    direction = "south" if track is _corridor().south else "north"
    best, distance = _nearest(direction, float(place[0]), float(place[1]))
    if distance > SNAP_KM:
        raise _error(
            "outside_coverage",
            f"point {list(place)!r} is {distance:.0f} km from the route, outside v1 "
            f"coverage (max {SNAP_KM:.0f} km).",
        )
    return best


@lru_cache(maxsize=4096)
def _nearest(direction: str, lon: float, lat: float) -> tuple[int, float]:
    """(vertex index, km) of the route vertex nearest a point. Cached: validate_plan asks for
    the same overnight and stop points many times."""
    track: _Track = getattr(_corridor(), direction)
    distances = [_haversine_km((lon, lat), c) for c in track.coords]
    best = min(range(len(distances)), key=distances.__getitem__)
    return best, distances[best]


def _label(place: Place) -> str | list[float]:
    return _resolve_town(place) if isinstance(place, str) else [float(v) for v in place]


def get_route(origin: Place, destination: Place, waypoints: Sequence[Place] = ()) -> dict[str, Any]:
    """Route between two places on the corridor, in either direction.

    Returns {origin, destination, geometry (GeoJSON LineString, [lon, lat]), distance_km,
    duration_min, legs: [{from, to, distance_km, duration_min}]}. Waypoints must lie strictly
    between origin and destination, in travel order.
    """
    corridor = _corridor()
    origin_label, destination_label = _label(origin), _label(destination)
    if _vertex(corridor.south, origin) == _vertex(corridor.south, destination):
        raise _error("same_place", f"origin and destination are the same place: {origin_label}")
    forward = _vertex(corridor.south, origin) < _vertex(corridor.south, destination)
    track = corridor.south if forward else corridor.north
    start, end = _vertex(track, origin), _vertex(track, destination)

    last = start
    for waypoint in waypoints:
        k = _vertex(track, waypoint)
        if not last < k < end:
            raise _error(
                "bad_waypoint",
                f"waypoint {_label(waypoint)!r} is not between the previous stop and "
                f"{destination_label} on the corridor",
            )
        last = k

    legs: list[dict[str, Any]] = []
    for k in range(len(track.boundaries) - 1):
        lo, hi = max(start, track.boundaries[k]), min(end, track.boundaries[k + 1])
        if lo >= hi:
            continue
        legs.append(
            {
                "from": track.towns[k] if lo == track.boundaries[k] else origin_label,
                "to": track.towns[k + 1] if hi == track.boundaries[k + 1] else destination_label,
                "distance_km": round(track.cum_km[hi] - track.cum_km[lo], 1),
                "duration_min": round(track.cum_min[hi] - track.cum_min[lo], 1),
            }
        )

    return {
        "origin": origin_label,
        "destination": destination_label,
        "geometry": {
            "type": "LineString",
            "coordinates": [list(c) for c in track.coords[start : end + 1]],
        },
        "distance_km": round(sum(leg["distance_km"] for leg in legs), 1),
        "duration_min": round(sum(leg["duration_min"] for leg in legs), 1),
        "legs": legs,
    }


CORRIDOR_TOWNS: dict[str, list[float]] = _corridor().points
