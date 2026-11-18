"""Build db/seed/stops.csv from the observed stop list (run by hand; commit the output).

    uv run python scripts/build_seed_stops.py

Input:  db/seed/sources/stops_observed.csv
        Name, type, address, published price and overnight policy, as stated on each place's
        own website (`source_url`). See db/seed/README.md for the sourcing rules.
Output: db/seed/stops.csv (loader format) and db/seed/sources/stops_geocoded.json.

Coordinates come from OpenStreetMap Nominatim, 1 request/second (ADR-002 amendment).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from build_seed_hotels import _result, _search, geocode  # same directory as this script

SEED = Path(__file__).resolve().parent.parent / "db" / "seed"
OBSERVED = SEED / "sources" / "stops_observed.csv"
GEOCODED = SEED / "sources" / "stops_geocoded.json"
OUT = SEED / "stops.csv"
# OSM categories that mean "we found the place itself", not just its street.
PLACE_CATEGORIES = ("tourism", "amenity", "craft", "shop", "leisure", "historic", "building")
COLUMNS = [
    "id",
    "name",
    "type",
    "lon",
    "lat",
    "overnight_ok",
    "est_cost_usd",
    "description",
    "source",
    "source_url",
]


def main() -> None:
    with OBSERVED.open(newline="", encoding="utf-8") as f:
        observed = list(csv.DictReader(f))

    geocoded: dict[str, dict[str, Any]] = {}
    for row in observed:
        if row.get("geocode_query"):
            # Explicit query, used where OSM lacks the place or the default search picks the
            # wrong feature. Recorded as an "override" match for review.
            geocoded[row["id"]] = _result(_search(row["geocode_query"])[0], "override")
        else:
            geocoded[row["id"]] = geocode(row["name"], row["address"], PLACE_CATEGORIES)
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
                    "type": row["type"],
                    "lon": g["lon"],
                    "lat": g["lat"],
                    "overnight_ok": row["overnight_ok"],
                    "est_cost_usd": row["est_cost_usd"],
                    "description": row["description"],
                    "source": "sample_public",
                    "source_url": row["source_url"],
                }
            )
    os.replace(tmp, OUT)
    GEOCODED.write_text(json.dumps(geocoded, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(observed)} stops)")


if __name__ == "__main__":
    main()
