"""Build the cached v1 corridor route (ADR-002). Run by hand once; commit the output.

    uv run python scripts/build_corridor.py

1. Geocodes each corridor town with OpenStreetMap Nominatim (1 request/second, per its
   usage policy) and records the result and its source URL.
2. Routes each town-to-town leg with the public OSRM demo server (car profile).
3. Writes db/seed/corridor_route.geojson: one LineString feature per leg, with distance_km and
   duration_min from OSRM, plus the geocoded towns.

Nothing calls these services at runtime; agent/tools/get_route.py reads the file.
Map data © OpenStreetMap contributors (ODbL).
"""

from __future__ import annotations

import itertools
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "db" / "seed" / "corridor_route.geojson"
USER_AGENT = "detour-poc-corridor-builder/0.1"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving"

# Corridor order (spec §6.1). Queries are what we send to the geocoder.
TOWNS = [
    ("Washington, DC", "Washington, District of Columbia, United States"),
    ("Front Royal, VA", "Front Royal, Warren County, Virginia, United States"),
    ("Luray, VA", "Luray, Page County, Virginia, United States"),
    ("Staunton, VA", "Staunton, Virginia, United States"),
    ("Roanoke, VA", "Roanoke, Virginia, United States"),
    ("Boone, NC", "Boone, Watauga County, North Carolina, United States"),
    ("Asheville, NC", "Asheville, Buncombe County, North Carolina, United States"),
]


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def geocode(query: str) -> dict[str, Any]:
    params = {"q": query, "format": "jsonv2", "limit": "1", "countrycodes": "us"}
    url = f"{NOMINATIM}?{urllib.parse.urlencode(params)}"
    results = fetch_json(url)
    if not results:
        raise SystemExit(f"no geocoding result for {query!r}")
    top = results[0]
    return {
        "lon": round(float(top["lon"]), 5),
        "lat": round(float(top["lat"]), 5),
        "osm": f"{top['osm_type']}/{top['osm_id']}",
        "display_name": top["display_name"],
        "source_url": url,
    }


def route_leg(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    url = (
        f"{OSRM}/{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
        "?overview=full&geometries=geojson&steps=false"
    )
    data = fetch_json(url)
    if data.get("code") != "Ok":
        raise SystemExit(f"OSRM error for {url}: {data.get('code')}")
    route = data["routes"][0]
    coords = [[round(lon, 5), round(lat, 5)] for lon, lat in route["geometry"]["coordinates"]]
    # Drop consecutive duplicates created by rounding.
    deduped = [c for i, c in enumerate(coords) if i == 0 or c != coords[i - 1]]
    return {
        "coordinates": deduped,
        "distance_km": round(route["distance"] / 1000, 1),
        "duration_min": round(route["duration"] / 60, 1),
        "source_url": url,
    }


def main() -> None:
    towns: dict[str, dict[str, Any]] = {}
    for name, query in TOWNS:
        towns[name] = geocode(query)
        print(f"geocoded {name}: {towns[name]['lon']}, {towns[name]['lat']}")
        time.sleep(1.1)

    features = []
    names = [name for name, _ in TOWNS]
    for start, end in itertools.pairwise(names):
        leg = route_leg(towns[start], towns[end])
        print(f"routed {start} -> {end}: {leg['distance_km']} km, {leg['duration_min']} min")
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": leg["coordinates"]},
                "properties": {
                    "from": start,
                    "to": end,
                    "distance_km": leg["distance_km"],
                    "duration_min": leg["duration_min"],
                    "source_url": leg["source_url"],
                },
            }
        )
        time.sleep(1.1)

    collection = {
        "type": "FeatureCollection",
        "properties": {
            "description": "Detour v1 corridor, one feature per town-to-town leg (ADR-002).",
            "attribution": "Map data © OpenStreetMap contributors (ODbL). "
            "Routing: OSRM demo server, car profile. Geocoding: Nominatim.",
            "towns": towns,
        },
        "features": features,
    }
    OUT.write_text(json.dumps(collection, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
