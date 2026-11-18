"""Database tests. They need PostGIS at DATABASE_URL and run in CI's `spatial` job.

Each test runs in its own throwaway schema (dropped afterwards), so pointing DATABASE_URL at a
development database never touches its real tables.
"""

import os
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from db.migrate import MIGRATIONS_DIR, apply_migrations
from db.seed.load_seed import FIXTURE_DIR, load, read_hotels, read_stops

pytestmark = pytest.mark.db

TABLES = ("hotels", "stops", "trips", "schema_migrations")


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set; db tests need a PostGIS database")
    return url


@pytest.fixture
def schema() -> Iterator[str]:
    """A unique, empty schema owned by this test, dropped afterwards."""
    name = f"detour_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_url(), autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def conn(schema: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(_url(), autocommit=True, options=f"-c search_path={schema},public") as c:
        yield c


def test_migrations_create_tables_and_indexes(conn: psycopg.Connection, schema: str) -> None:
    assert apply_migrations(conn, MIGRATIONS_DIR) == ["0001_init"]
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)
        )
    }
    assert set(TABLES) <= tables
    indexes = {
        row[0]
        for row in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s", (schema,))
    }
    assert {"hotels_geom_gix", "hotels_geog_gix", "stops_geom_gix", "stops_geog_gix"} <= indexes


def test_migrations_rerun_is_noop(conn: psycopg.Connection) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    assert apply_migrations(conn, MIGRATIONS_DIR) == []


def test_migrations_need_autocommit_connection(schema: str) -> None:
    with (
        psycopg.connect(_url(), options=f"-c search_path={schema},public") as c,
        pytest.raises(ValueError, match="autocommit"),
    ):
        apply_migrations(c, MIGRATIONS_DIR)


def test_applied_migration_survives_a_later_failure(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE kept (id int);")
    (tmp_path / "0002_broken.sql").write_text("CREATE TABLE lost (id int); SELECT 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        apply_migrations(conn, tmp_path)
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == ["0001_ok"]
    assert conn.execute("SELECT to_regclass('kept') IS NOT NULL").fetchone() == (True,)
    assert conn.execute("SELECT to_regclass('lost') IS NULL").fetchone() == (True,)


HOTEL_SQL = (
    "INSERT INTO hotels (id, name, brand_segment, geom, price_usd, points_per_night, "
    "source, source_url) VALUES (%s, 'X', 'economy', "
    "ST_SetSRID(ST_MakePoint(-78.4, 38.6), 4326), %s, 8000, %s, %s)"
)


@pytest.mark.parametrize(
    ("hotel_id", "price", "source", "url"),
    [
        ("hotel_ok", Decimal("-1"), "sample_public", "https://example.com"),
        ("hotel_ok", Decimal("NaN"), "sample_public", "https://example.com"),
        ("hotel_ok", Decimal("90"), "scraped", "https://example.com"),
        ("hotel_ok", Decimal("90"), "sample_public", "http://example.com"),
        ("hotel_ok", Decimal("90"), "sample_public", "https://"),
        ("bad id", Decimal("90"), "sample_public", "https://example.com"),
    ],
)
def test_hotel_constraints_reject_bad_rows(
    conn: psycopg.Connection, hotel_id: str, price: Decimal, source: str, url: str
) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(HOTEL_SQL, (hotel_id, price, source, url))


@pytest.mark.parametrize(
    ("name", "lon", "lat"),
    [("\t\n", -78.4, 38.6), ("X", -200.0, 38.6), ("X", -78.4, 95.0)],
)
def test_hotel_rejects_blank_name_and_invalid_coordinates(
    conn: psycopg.Connection, name: str, lon: float, lat: float
) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO hotels (id, name, brand_segment, geom, price_usd, points_per_night, "
            "source, source_url) VALUES ('hotel_ok', %s, 'economy', "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326), 90, 8000, 'sample_public', "
            "'https://example.com')",
            (name, lon, lat),
        )


def test_stop_cost_rejects_nan(conn: psycopg.Connection) -> None:
    apply_migrations(conn, MIGRATIONS_DIR)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO stops (id, name, type, geom, overnight_ok, est_cost_usd, source, "
            "source_url) VALUES ('stop_x', 'X', 'farm', "
            "ST_SetSRID(ST_MakePoint(-78.4, 38.6), 4326), false, 'NaN', "
            "'sample_public', 'https://example.com')"
        )


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
