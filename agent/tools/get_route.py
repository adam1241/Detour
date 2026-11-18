"""get_route tool (spec §5.3, ADR-002).

Serves routes between corridor towns from the cached OSRM result in
db/seed/corridor_route.geojson (built once by scripts/build_corridor.py). No network calls.
v1 covers one corridor; any other place is rejected as outside coverage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

CORRIDOR_FILE = Path(__file__).resolve().parents[2] / "db" / "seed" / "corridor_route.geojson"

STATE_WORDS = {
    "VA": ("va", "virginia"),
    "NC": ("nc", "north carolina"),
}


class RouteError(ValueError):
    """The request can't be answered from the v1 corridor."""


@dataclass(frozen=True)
class _Leg:
    start: str
    end: str
    distance_km: float
    duration_min: float
    coordinates: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class _Corridor:
    towns: tuple[str, ...]
    points: dict[str, list[float]]
    legs: tuple[_Leg, ...]
    aliases: dict[str, str]


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _aliases_for(town: str) -> set[str]:
    city, _, state = town.partition(", ")
    if state == "DC":
        return {"washington dc", "washington", "dc", "washington d c", "district of columbia"}
    base = _normalize(city)
    return {base} | {f"{base} {_normalize(word)}" for word in STATE_WORDS[state]}


@cache
def _corridor() -> _Corridor:
    data: dict[str, Any] = json.loads(CORRIDOR_FILE.read_text(encoding="utf-8"))
    legs = tuple(
        _Leg(
            start=f["properties"]["from"],
            end=f["properties"]["to"],
            distance_km=float(f["properties"]["distance_km"]),
            duration_min=float(f["properties"]["duration_min"]),
            coordinates=tuple((float(x), float(y)) for x, y in f["geometry"]["coordinates"]),
        )
        for f in data["features"]
    )
    towns = (legs[0].start, *(leg.end for leg in legs))
    points = {name: [t["lon"], t["lat"]] for name, t in data["properties"]["towns"].items()}
    aliases = {alias: town for town in towns for alias in _aliases_for(town)}
    return _Corridor(towns=towns, points=points, legs=legs, aliases=aliases)


def _resolve(place: str) -> str:
    town = _corridor().aliases.get(_normalize(place))
    if town is None:
        towns = ", ".join(_corridor().towns)
        raise RouteError(f"{place!r} is outside v1 coverage. Corridor towns: {towns}")
    return town


def get_route(origin: str, destination: str, waypoints: Sequence[str] = ()) -> dict[str, Any]:
    """Route between two corridor towns, in either direction.

    Returns {origin, destination, geometry (GeoJSON LineString, [lon, lat]), distance_km,
    duration_min, legs: [{from, to, distance_km, duration_min}]}. Waypoints must be corridor
    towns strictly between origin and destination, in travel order.
    """
    corridor = _corridor()
    start, end = _resolve(origin), _resolve(destination)
    if start == end:
        raise RouteError(f"origin and destination are the same place: {start}")
    i, j = corridor.towns.index(start), corridor.towns.index(end)
    forward = i < j

    last = i
    for waypoint in waypoints:
        k = corridor.towns.index(_resolve(waypoint))
        between = (last < k < j) if forward else (j < k < last)
        if not between:
            raise RouteError(
                f"waypoint {waypoint!r} is not between the previous stop and {end} on the corridor"
            )
        last = k

    selected = corridor.legs[i:j] if forward else corridor.legs[j:i][::-1]
    legs_out: list[dict[str, Any]] = []
    coordinates: list[list[float]] = []
    for leg in selected:
        coords = leg.coordinates if forward else leg.coordinates[::-1]
        frm, to = (leg.start, leg.end) if forward else (leg.end, leg.start)
        # Legs share their joining point; don't repeat it.
        coordinates.extend([list(c) for c in (coords if not coordinates else coords[1:])])
        legs_out.append(
            {
                "from": frm,
                "to": to,
                "distance_km": leg.distance_km,
                "duration_min": leg.duration_min,
            }
        )

    return {
        "origin": start,
        "destination": end,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "distance_km": round(sum(leg["distance_km"] for leg in legs_out), 1),
        "duration_min": round(sum(leg["duration_min"] for leg in legs_out), 1),
        "legs": legs_out,
    }


CORRIDOR_TOWNS: dict[str, list[float]] = _corridor().points
