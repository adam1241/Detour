"""Database tests. They need PostGIS at DATABASE_URL and run in CI's `spatial` job."""

import os
from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR, apply_migrations
from db.seed.load_seed import FIXTURE_DIR, load, read_hotels, read_stops

pytestmark = pytest.mark.db

TABLES = ("hotels", "stops", "trips", "schema_migrations")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set; db tests need a PostGIS database")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS hotels, stops, trips, schema_migrations CASCADE")
        yield c


def test_migrations_create_tables_and_indexes(conn: psycopg.Connection) -> None:
    assert apply_migrations(conn, MIGRATIONS_DIR) == ["0001_init"]
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert set(TABLES) <= tables
    indexes = {row[0] for row in conn.execute("SELECT indexname FROM pg_indexes")}
    assert {"hotels_geom_gix", "hotels_geog_gix", "stops_geom_gix", "stops_geog_gix"} <= indexes


def test_migrations_rerun_is_noop(conn: psycopg.Connection) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    assert apply_migrations(conn, MIGRATIONS_DIR) == []


HOTEL_SQL = (
    "INSERT INTO hotels (id, name, brand_segment, geom, price_usd, points_per_night, "
    "source, source_url) VALUES (%s, 'X', 'economy', "
    "ST_SetSRID(ST_MakePoint(-78.4, 38.6), 4326), %s, 8000, %s, %s)"
)


@pytest.mark.parametrize(
    ("hotel_id", "price", "source", "url"),
    [
        ("hotel_ok", Decimal("-1"), "sample_public", "https://example.com"),
        ("hotel_ok", Decimal("90"), "scraped", "https://example.com"),
        ("hotel_ok", Decimal("90"), "sample_public", "http://example.com"),
        ("bad id", Decimal("90"), "sample_public", "https://example.com"),
    ],
)
def test_hotel_constraints_reject_bad_rows(
    conn: psycopg.Connection, hotel_id: str, price: Decimal, source: str, url: str
) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(HOTEL_SQL, (hotel_id, price, source, url))


def test_trip_vehicle_must_be_rv_or_car(conn: psycopg.Connection) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.execute("INSERT INTO trips (request_text, vehicle) VALUES ('x', 'car')")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO trips (request_text, vehicle) VALUES ('x', 'bike')")


def test_fixture_load_is_idempotent(conn: psycopg.Connection) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    hotels = read_hotels(FIXTURE_DIR / "hotels.csv")
    stops = read_stops(FIXTURE_DIR / "stops.csv")
    load(conn, hotels, stops, allow_fixture=True)
    load(conn, hotels, stops, allow_fixture=True)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM hotels), (SELECT count(*) FROM stops)"
    ).fetchone()
    assert counts == (len(hotels), len(stops))
    lon_lat = conn.execute(
        "SELECT ST_X(geom), ST_Y(geom) FROM hotels WHERE id = %s", (hotels[0].id,)
    ).fetchone()
    assert lon_lat == pytest.approx((hotels[0].lon, hotels[0].lat))
