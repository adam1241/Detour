"""Import Choice-brand hotels and outdoor/culture stops from OpenStreetMap (ADR-008).

    uv run python -m db.seed.import_osm [--states CO UT] [--out db/seed/imported]

One Overpass query per state and kind (about 100 queries for a full run, well inside the
public server's fair-use guidance), with a pause between queries. Writes hotels_osm.csv and
stops_osm.csv in the loader's format, plus counts_osm.md. Imported rows never carry prices.
Data: (c) OpenStreetMap contributors, ODbL 1.0.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from db.seed.import_common import (
    OUT_DIR,
    STATES,
    http_json,
    in_us,
    report,
    write_hotels,
    write_stops,
)
from db.seed.load_seed import HotelRow, StopRow, validate

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PAUSE_S = 5.0

# Choice Hotels brands as tagged in OSM (`brand=`), mapped to the schema's three segments the
# same way the hand-sourced seed rows are classified (upper midscale counts as upscale).
BRAND_SEGMENTS: dict[str, str] = {
    "Econo Lodge": "economy",
    "Rodeway Inn": "economy",
    "Suburban Studios": "economy",
    "WoodSpring Suites": "economy",
    "Quality Inn": "midscale",
    "Quality Inn & Suites": "midscale",
    "Sleep Inn": "midscale",
    "Clarion Pointe": "midscale",
    "MainStay Suites": "midscale",
    "Everhome Suites": "midscale",
    "Comfort Inn": "upscale",
    "Comfort Inn & Suites": "upscale",
    "Comfort Suites": "upscale",
    "Clarion": "upscale",
    "Cambria Hotels": "upscale",
    "Ascend Hotel Collection": "upscale",
}

# OSM tag -> stop type. Imported stops are daytime stops only (overnight_ok = false): OSM says
# nothing about whether a winery or farm hosts RVs (spec §6.1).
STOP_TAGS: tuple[tuple[str, str, str], ...] = (
    ("craft", "winery", "winery"),
    ("craft", "brewery", "brewery"),
    ("tourism", "museum", "museum"),
    ("shop", "farm", "farm"),
)


def overpass_query(state: str, kind: str) -> str:
    area = f'area["ISO3166-2"="US-{state}"][admin_level=4]->.s;'
    if kind == "hotels":
        brands = "|".join(BRAND_SEGMENTS)
        body = f'nwr["tourism"~"^(hotel|motel)$"]["brand"~"^({brands})$"](area.s);'
    else:
        body = "".join(f'nwr["{k}"="{v}"]["name"](area.s);' for k, v, _ in STOP_TAGS)
    return f"[out:json][timeout:240];{area}({body});out center tags;"


def _point(el: dict[str, Any]) -> tuple[float, float] | None:
    src = el if "lat" in el else el.get("center")
    if not src or "lat" not in src or "lon" not in src:
        return None
    return float(src["lon"]), float(src["lat"])


def _ref(el: dict[str, Any]) -> tuple[str, str, str]:
    """(id suffix, source_ref, source_url) for an OSM element."""
    kind, osm_id = el["type"], int(el["id"])
    return (
        f"{kind}_{osm_id}",
        f"osm:{kind}/{osm_id}",
        f"https://www.openstreetmap.org/{kind}/{osm_id}",
    )


def hotel_rows(elements: Iterable[dict[str, Any]]) -> list[HotelRow]:
    rows: dict[str, HotelRow] = {}
    for el in elements:
        tags = el.get("tags") or {}
        brand = tags.get("brand", "")
        point = _point(el)
        if brand not in BRAND_SEGMENTS or point is None or not in_us(*point):
            continue
        suffix, ref, url = _ref(el)
        rows[ref] = HotelRow(
            id=f"hotel_osm_{suffix}",
            name=(tags.get("name") or brand).strip(),
            brand_segment=BRAND_SEGMENTS[brand],
            lon=point[0],
            lat=point[1],
            price_usd=None,
            points_per_night=None,
            amenities=(),
            source="osm",
            source_url=url,
            source_ref=ref,
            licence="ODbL-1.0",
        )
    return list(rows.values())


def _stop_type(tags: dict[str, str]) -> str | None:
    for key, value, stop_type in STOP_TAGS:
        if tags.get(key) == value:
            return stop_type
    return None


def stop_rows(elements: Iterable[dict[str, Any]]) -> list[StopRow]:
    rows: dict[str, StopRow] = {}
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        stop_type = _stop_type(tags)
        point = _point(el)
        if not name or stop_type is None or point is None or not in_us(*point):
            continue
        suffix, ref, url = _ref(el)
        rows[ref] = StopRow(
            id=f"stop_osm_{suffix}",
            name=name,
            type=stop_type,
            lon=point[0],
            lat=point[1],
            overnight_ok=False,
            est_cost_usd=None,
            description="",
            source="osm",
            source_url=url,
            source_ref=ref,
            licence="ODbL-1.0",
        )
    return list(rows.values())


def fetch(state: str, kind: str, cache_dir: Path) -> list[dict[str, Any]]:
    """Overpass elements for one state and kind, cached on disk so a rerun resumes."""
    cache = cache_dir / f"osm_{state}_{kind}.json"
    if cache.exists():
        cached: list[dict[str, Any]] = json.loads(cache.read_text(encoding="utf-8"))
        return cached
    data = http_json(OVERPASS_URL, data={"data": overpass_query(state, kind)})
    elements: list[dict[str, Any]] = data.get("elements", [])
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(elements), encoding="utf-8")
    tmp.replace(cache)
    time.sleep(PAUSE_S)  # only after a real request
    return elements


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--states", nargs="*", default=list(STATES))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    hotels: dict[str, HotelRow] = {}
    stops: dict[str, StopRow] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for state in args.states:
        for kind in ("hotels", "stops"):
            elements = fetch(state, kind, args.out / "cache")
            if kind == "hotels":
                new_hotels = [h for h in hotel_rows(elements) if h.source_ref not in hotels]
                hotels.update({h.source_ref or h.id: h for h in new_hotels})
                counts[(state, "hotel")] += len(new_hotels)
            else:
                new_stops = [s for s in stop_rows(elements) if s.source_ref not in stops]
                stops.update({s.source_ref or s.id: s for s in new_stops})
                for s in new_stops:
                    counts[(state, s.type)] += 1
            print(f"{state} {kind}: {len(elements)} elements", flush=True)

    hotel_list, stop_list = list(hotels.values()), list(stops.values())
    errors = validate(hotel_list, stop_list)
    if errors:
        print("\n".join(errors[:20]), file=sys.stderr)
        return 1
    write_hotels(args.out / "hotels_osm.csv", hotel_list)
    write_stops(args.out / "stops_osm.csv", stop_list)
    table = report(counts)
    (args.out / "counts_osm.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(f"wrote {len(hotel_list)} hotels and {len(stop_list)} stops to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
