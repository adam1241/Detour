"""Read, validate and load the sample hotels and stops (spec v0.3 §6).

Usage: DATABASE_URL=postgresql://... uv run python -m db.seed.load_seed
Loading is idempotent (insert or update by id). Rows tagged 'test_fixture' are refused here;
only tests may load them.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

SEED_DIR = Path(__file__).parent
FIXTURE_DIR = SEED_DIR / "fixtures"

# Generous box around DC -> Front Royal -> Luray -> Staunton -> Roanoke -> Boone -> Asheville.
CORRIDOR_BBOX = {"min_lon": -83.0, "max_lon": -76.8, "min_lat": 35.3, "max_lat": 39.2}
BRAND_SEGMENTS = {"economy", "midscale", "upscale"}
STOP_TYPES = {"winery", "farm", "brewery", "museum"}
SOURCES = {"sample_public", "test_fixture"}

HOTEL_COLUMNS = [
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
STOP_COLUMNS = [
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


@dataclass(frozen=True)
class HotelRow:
    id: str
    name: str
    brand_segment: str
    lon: float
    lat: float
    price_usd: Decimal
    points_per_night: int
    amenities: tuple[str, ...]
    source: str
    source_url: str


@dataclass(frozen=True)
class StopRow:
    id: str
    name: str
    type: str
    lon: float
    lat: float
    overnight_ok: bool
    est_cost_usd: Decimal
    description: str
    source: str
    source_url: str


def _read(path: Path, columns: list[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != columns:
            raise ValueError(f"{path.name}: columns must be {columns}, got {reader.fieldnames}")
        return list(reader)


def _decimal(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as e:
        raise ValueError(f"{field}: not a number: {value!r}") from e


def _bool(value: str, field: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError(f"{field}: expected true or false, got {value!r}")
    return value == "true"


def read_hotels(path: Path) -> list[HotelRow]:
    return [
        HotelRow(
            id=r["id"],
            name=r["name"],
            brand_segment=r["brand_segment"],
            lon=float(r["lon"]),
            lat=float(r["lat"]),
            price_usd=_decimal(r["price_usd"], "price_usd"),
            points_per_night=int(r["points_per_night"]),
            amenities=tuple(a for a in r["amenities"].split(";") if a),
            source=r["source"],
            source_url=r["source_url"],
        )
        for r in _read(path, HOTEL_COLUMNS)
    ]


def read_stops(path: Path) -> list[StopRow]:
    return [
        StopRow(
            id=r["id"],
            name=r["name"],
            type=r["type"],
            lon=float(r["lon"]),
            lat=float(r["lat"]),
            overnight_ok=_bool(r["overnight_ok"], "overnight_ok"),
            est_cost_usd=_decimal(r["est_cost_usd"], "est_cost_usd"),
            description=r["description"],
            source=r["source"],
            source_url=r["source_url"],
        )
        for r in _read(path, STOP_COLUMNS)
    ]


def _common_errors(row: HotelRow | StopRow, prefix: str) -> list[str]:
    errors = []
    if not re.fullmatch(rf"{prefix}_[a-z0-9_]+", row.id):
        errors.append(f"{row.id}: id must match {prefix}_[a-z0-9_]+")
    if not row.name.strip():
        errors.append(f"{row.id}: name is empty")
    box = CORRIDOR_BBOX
    if not (
        box["min_lon"] <= row.lon <= box["max_lon"] and box["min_lat"] <= row.lat <= box["max_lat"]
    ):
        errors.append(f"{row.id}: point ({row.lon}, {row.lat}) is outside the corridor bbox")
    if row.source not in SOURCES:
        errors.append(f"{row.id}: source must be one of {sorted(SOURCES)}")
    if not row.source_url.startswith("https://"):
        errors.append(f"{row.id}: source_url must start with https://")
    return errors


def validate(hotels: Sequence[HotelRow], stops: Sequence[StopRow]) -> list[str]:
    """Every problem found, as human-readable strings. Empty list = valid."""
    errors: list[str] = []
    seen: set[str] = set()
    rows: list[HotelRow | StopRow] = [*hotels, *stops]
    for row in rows:
        if row.id in seen:
            errors.append(f"{row.id}: duplicate id")
        seen.add(row.id)
    for h in hotels:
        errors += _common_errors(h, "hotel")
        if h.brand_segment not in BRAND_SEGMENTS:
            errors.append(f"{h.id}: brand_segment must be one of {sorted(BRAND_SEGMENTS)}")
        if h.price_usd <= 0:
            errors.append(f"{h.id}: price_usd must be > 0")
        if h.points_per_night < 0:
            errors.append(f"{h.id}: points_per_night must be >= 0")
    for s in stops:
        errors += _common_errors(s, "stop")
        if s.type not in STOP_TYPES:
            errors.append(f"{s.id}: type must be one of {sorted(STOP_TYPES)}")
        if s.est_cost_usd < 0:
            errors.append(f"{s.id}: est_cost_usd must be >= 0")
    return errors


def check_sources(
    hotels: Sequence[HotelRow], stops: Sequence[StopRow], *, allow_fixture: bool
) -> None:
    """Refuse test_fixture rows unless explicitly allowed (tests only)."""
    if allow_fixture:
        return
    rows: list[HotelRow | StopRow] = [*hotels, *stops]
    fixtures = [r.id for r in rows if r.source == "test_fixture"]
    if fixtures:
        raise ValueError(f"refusing test_fixture rows outside tests: {fixtures}")


HOTEL_UPSERT = """
INSERT INTO hotels (id, name, brand_segment, geom, price_usd, points_per_night, amenities,
                    source, source_url)
VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, brand_segment = EXCLUDED.brand_segment, geom = EXCLUDED.geom,
    price_usd = EXCLUDED.price_usd, points_per_night = EXCLUDED.points_per_night,
    amenities = EXCLUDED.amenities, source = EXCLUDED.source, source_url = EXCLUDED.source_url
"""

STOP_UPSERT = """
INSERT INTO stops (id, name, type, geom, overnight_ok, est_cost_usd, description,
                   source, source_url)
VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, type = EXCLUDED.type, geom = EXCLUDED.geom,
    overnight_ok = EXCLUDED.overnight_ok, est_cost_usd = EXCLUDED.est_cost_usd,
    description = EXCLUDED.description, source = EXCLUDED.source, source_url = EXCLUDED.source_url
"""


def load(
    conn: psycopg.Connection,
    hotels: Sequence[HotelRow],
    stops: Sequence[StopRow],
    *,
    allow_fixture: bool = False,
) -> None:
    """Validate, then insert or update every row in one transaction."""
    errors = validate(hotels, stops)
    if errors:
        raise ValueError("invalid seed data:\n" + "\n".join(errors))
    check_sources(hotels, stops, allow_fixture=allow_fixture)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            HOTEL_UPSERT,
            [
                (
                    h.id,
                    h.name,
                    h.brand_segment,
                    h.lon,
                    h.lat,
                    h.price_usd,
                    h.points_per_night,
                    list(h.amenities),
                    h.source,
                    h.source_url,
                )
                for h in hotels
            ],
        )
        cur.executemany(
            STOP_UPSERT,
            [
                (
                    s.id,
                    s.name,
                    s.type,
                    s.lon,
                    s.lat,
                    s.overnight_ok,
                    s.est_cost_usd,
                    s.description,
                    s.source,
                    s.source_url,
                )
                for s in stops
            ],
        )


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    hotels = read_hotels(SEED_DIR / "hotels.csv")
    stops = read_stops(SEED_DIR / "stops.csv")
    with psycopg.connect(url) as conn:
        load(conn, hotels, stops)
    print(f"loaded {len(hotels)} hotels and {len(stops)} stops")
    return 0


if __name__ == "__main__":
    sys.exit(main())
