"""Build db/seed/hotels.csv from the observed hotel list (run by hand; commit the output).

    uv run python scripts/build_seed_hotels.py

Input:  db/seed/sources/hotels_observed.csv
        Name, address, Best Available cash rate and Reward Points rate, as shown on the
        choicehotels.com search page in `source_url` for one sample night (see db/seed/README.md).
Output: db/seed/hotels.csv (loader format) and db/seed/sources/hotels_geocoded.json
        (the Nominatim result and request URL for every address, for traceability).

Coordinates come from OpenStreetMap Nominatim, 1 request/second (ADR-002 amendment).
"""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SEED = Path(__file__).resolve().parent.parent / "db" / "seed"
OBSERVED = SEED / "sources" / "hotels_observed.csv"
GEOCODED = SEED / "sources" / "hotels_geocoded.json"
OUT = SEED / "hotels.csv"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "detour-poc-seed-builder/0.1"
COLUMNS = [
    "id",
    "name",
    "brand_segment",
    "lon",
    "lat",
    "price_usd",
    "points_per_night",
    "amenities",
    "source",
    "source_url",
]


def _search(query: str) -> list[dict[str, Any]]:
    params = {"q": query, "format": "jsonv2", "limit": "3", "countrycodes": "us"}
    url = f"{NOMINATIM}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        results: list[dict[str, Any]] = json.load(response)
    time.sleep(1.1)
    for r in results:
        r["_url"] = url
    return results


def geocode(name: str, address: str, accept: tuple[str, ...] = ("tourism",)) -> dict[str, Any]:
    """Prefer an exact place match (OSM feature in `accept` categories) by name + town; else
    the address.

    The match type is recorded so a reviewer can see which rows are only street-level.
    """
    town = address.split(",", 1)[1].strip()
    short = name.split(",")[0].split(" - ")[0].strip()  # "Graystone Lodge", "Sleep Inn Boone ..."
    brand = " ".join(name.split()[:2])  # "Sleep Inn"
    queries = list(dict.fromkeys([f"{name}, {town}", f"{short}, {town}", f"{brand}, {town}"]))
    queries.append(address)
    fallback: dict[str, Any] | None = None
    for query in queries:
        for r in _search(query):
            if r.get("category") in accept:
                return _result(r, "place")
            if fallback is None and query == address:
                fallback = r
    if fallback is not None:
        return _result(fallback, "address")
    # Last resort: the street without its house number (recorded as a coarse "street" match).
    street_only = address.split(" ", 1)[1] if address[:1].isdigit() else address
    for r in _search(street_only):
        return _result(r, "street")
    # Final fallback: the town itself (coarse, a few km). Flagged as "town" for review.
    town_only = address.split(",", 1)[1].strip()
    for r in _search(town_only.rsplit(" ", 1)[0] if town_only[-5:].isdigit() else town_only):
        return _result(r, "town")
    raise SystemExit(f"no geocoding result for {name!r} / {address!r}")


def _result(r: dict[str, Any], match: str) -> dict[str, Any]:
    return {
        "lon": round(float(r["lon"]), 6),
        "lat": round(float(r["lat"]), 6),
        "display_name": r["display_name"],
        "osm": f"{r['osm_type']}/{r['osm_id']}",
        "match": match,
        "geocode_url": r["_url"],
    }


def main() -> None:
    with OBSERVED.open(newline="", encoding="utf-8") as f:
        observed = list(csv.DictReader(f))

    geocoded: dict[str, dict[str, Any]] = {}
    for row in observed:
        geocoded[row["id"]] = geocode(row["name"], row["address"])
        g = geocoded[row["id"]]
        print(f"{row['id']}: {g['lon']}, {g['lat']} ({g['match']}) {g['display_name'][:70]}")

    tmp = OUT.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in observed:
            g = geocoded[row["id"]]
            writer.writerow(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "brand_segment": row["brand_segment"],
                    "lon": g["lon"],
                    "lat": g["lat"],
                    "price_usd": row["price_usd"],
                    "points_per_night": row["points_per_night"],
                    "amenities": "",
                    "source": "sample_public",
                    "source_url": row["source_url"],
                }
            )
    os.replace(tmp, OUT)
    GEOCODED.write_text(json.dumps(geocoded, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(observed)} hotels)")


if __name__ == "__main__":
    main()
