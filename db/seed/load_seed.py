"""Read, validate and load hotels and stops (spec §6, ADR-008).

Hand-sourced rows ('sample_public') carry prices from a fetched page. Imported rows ('osm',
'ridb', ADR-008) carry an upstream id and licence and never a price.

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
from urllib.parse import urlsplit

import psycopg

SEED_DIR = Path(__file__).parent
FIXTURE_DIR = SEED_DIR / "fixtures"

# Contiguous US (ADR-008). Alaska, Hawaii and territories are out of scope.
US_BBOX = {"min_lon": -125.0, "max_lon": -66.9, "min_lat": 24.4, "max_lat": 49.4}
BRAND_SEGMENTS = {"economy", "midscale", "upscale"}
STOP_TYPES = {"winery", "farm", "brewery", "museum"}
HAND_SOURCED = {"sample_public", "test_fixture"}  # prices from a fetched page
LICENCES = {"osm": "ODbL-1.0", "ridb": "US-PD"}  # imported sources and their licence
SOURCES = HAND_SOURCED | set(LICENCES)
SOURCE_REF = re.compile(r"(osm:(node|way|relation)/[0-9]+|ridb:facility/[0-9]+)")

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
NATIONWIDE_COLUMNS = ["source_ref", "licence"]  # optional trailing columns (ADR-008)
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
    price_usd: Decimal | None
    points_per_night: int | None
    amenities: tuple[str, ...]
    source: str
    source_url: str
    source_ref: str | None = None
    licence: str | None = None


@dataclass(frozen=True)
class StopRow:
    id: str
    name: str
    type: str
    lon: float
    lat: float
    overnight_ok: bool
    est_cost_usd: Decimal | None
    description: str
    source: str
    source_url: str
    source_ref: str | None = None
    licence: str | None = None


def _read(path: Path, columns: list[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames not in (columns, [*columns, *NATIONWIDE_COLUMNS]):
            raise ValueError(
                f"{path.name}: columns must be {columns} (optionally + {NATIONWIDE_COLUMNS}), "
                f"got {reader.fieldnames}"
            )
        return list(reader)


def _optional(value: str | None) -> str | None:
    return value or None


def _decimal_or_none(value: str, field: str) -> Decimal | None:
    return None if value == "" else _decimal(value, field)


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
            price_usd=_decimal_or_none(r["price_usd"], "price_usd"),
            points_per_night=int(r["points_per_night"]) if r["points_per_night"] else None,
            amenities=tuple(a for a in r["amenities"].split(";") if a),
            source=r["source"],
            source_url=r["source_url"],
            source_ref=_optional(r.get("source_ref")),
            licence=_optional(r.get("licence")),
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
            est_cost_usd=_decimal_or_none(r["est_cost_usd"], "est_cost_usd"),
            description=r["description"],
            source=r["source"],
            source_url=r["source_url"],
            source_ref=_optional(r.get("source_ref")),
            licence=_optional(r.get("licence")),
        )
        for r in _read(path, STOP_COLUMNS)
    ]


MAX_MONEY = Decimal("999999.99")  # largest value numeric(8, 2) can hold
CENT = Decimal("0.01")


def _money_error(value: Decimal, field: str, *, min_exclusive_zero: bool) -> str | None:
    """Why `value` can't be stored exactly in numeric(8, 2) within range, or None if it can."""
    if not value.is_finite():
        return f"{field} must be a finite number, got {value}"
    if value != value.quantize(CENT):
        return f"{field} must have at most 2 decimal places, got {value}"
    if value > MAX_MONEY:
        return f"{field} must be <= {MAX_MONEY}, got {value}"
    if min_exclusive_zero and value <= 0:
        return f"{field} must be > 0, got {value}"
    if not min_exclusive_zero and value < 0:
        return f"{field} must be >= 0, got {value}"
    return None


def _common_errors(row: HotelRow | StopRow, prefix: str) -> list[str]:
    errors = []
    if not re.fullmatch(rf"{prefix}_[a-z0-9_]+", row.id):
        errors.append(f"{row.id}: id must match {prefix}_[a-z0-9_]+")
    if not row.name.strip():
        errors.append(f"{row.id}: name is empty")
    box = US_BBOX
    if not (
        box["min_lon"] <= row.lon <= box["max_lon"] and box["min_lat"] <= row.lat <= box["max_lat"]
    ):
        errors.append(f"{row.id}: point ({row.lon}, {row.lat}) is outside the contiguous US")
    if row.source not in SOURCES:
        errors.append(f"{row.id}: source must be one of {sorted(SOURCES)}")
    ref_ok = row.source_ref is None or SOURCE_REF.fullmatch(row.source_ref) is not None
    if row.source in LICENCES:
        if row.source_ref is None or not ref_ok:
            errors.append(f"{row.id}: source_ref must look like osm:node/123 or ridb:facility/456")
        if row.licence != LICENCES[row.source]:
            errors.append(f"{row.id}: licence must be {LICENCES[row.source]} for {row.source}")
    else:
        if not ref_ok:
            errors.append(f"{row.id}: source_ref must look like osm:node/123 or ridb:facility/456")
        if row.licence is not None:
            errors.append(f"{row.id}: licence is only set on imported (osm, ridb) rows")
    url = urlsplit(row.source_url)
    if url.scheme != "https" or not url.hostname:
        errors.append(f"{row.id}: source_url must be an https:// URL with a host")
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
        if h.source in HAND_SOURCED:
            if h.price_usd is None:
                errors.append(f"{h.id}: price_usd is required for hand-sourced rows")
            if h.points_per_night is None:
                errors.append(f"{h.id}: points_per_night is required for hand-sourced rows")
        elif h.price_usd is not None or h.points_per_night is not None:
            errors.append(f"{h.id}: prices only come from a fetched page, not from {h.source}")
        if h.price_usd is not None and (
            price_error := _money_error(h.price_usd, "price_usd", min_exclusive_zero=True)
        ):
            errors.append(f"{h.id}: {price_error}")
        if h.points_per_night is not None and h.points_per_night < 0:
            errors.append(f"{h.id}: points_per_night must be >= 0")
    for s in stops:
        errors += _common_errors(s, "stop")
        if s.type not in STOP_TYPES:
            errors.append(f"{s.id}: type must be one of {sorted(STOP_TYPES)}")
        if s.source in HAND_SOURCED:
            if s.est_cost_usd is None:
                errors.append(f"{s.id}: est_cost_usd is required for hand-sourced rows")
        elif s.est_cost_usd is not None:
            errors.append(f"{s.id}: prices only come from a fetched page, not from {s.source}")
        if s.est_cost_usd is not None and (
            cost_error := _money_error(s.est_cost_usd, "est_cost_usd", min_exclusive_zero=False)
        ):
            errors.append(f"{s.id}: {cost_error}")
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
                    source, source_url, source_ref, licence)
VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, brand_segment = EXCLUDED.brand_segment, geom = EXCLUDED.geom,
    price_usd = EXCLUDED.price_usd, points_per_night = EXCLUDED.points_per_night,
    amenities = EXCLUDED.amenities, source = EXCLUDED.source, source_url = EXCLUDED.source_url,
    source_ref = EXCLUDED.source_ref, licence = EXCLUDED.licence
"""

STOP_UPSERT = """
INSERT INTO stops (id, name, type, geom, overnight_ok, est_cost_usd, description,
                   source, source_url, source_ref, licence)
VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, type = EXCLUDED.type, geom = EXCLUDED.geom,
    overnight_ok = EXCLUDED.overnight_ok, est_cost_usd = EXCLUDED.est_cost_usd,
    description = EXCLUDED.description, source = EXCLUDED.source, source_url = EXCLUDED.source_url,
    source_ref = EXCLUDED.source_ref, licence = EXCLUDED.licence
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
    # Same lock order (by id) for every load, so concurrent loads can't deadlock.
    hotels = sorted(hotels, key=lambda h: h.id)
    stops = sorted(stops, key=lambda s: s.id)
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
                    h.source_ref,
                    h.licence,
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
                    s.source_ref,
                    s.licence,
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
    with psycopg.connect(
        url, connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))
    ) as conn:
        load(conn, hotels, stops)
    print(f"loaded {len(hotels)} hotels and {len(stops)} stops")
    return 0


if __name__ == "__main__":
    sys.exit(main())
