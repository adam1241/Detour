"""find_stops_along_route and find_hotels_near (spec §5.3, ADR-003).

Input validation tests run anywhere. Query tests are marked `db` and run against PostGIS in CI,
on synthetic test_fixture rows placed at known distances from a synthetic north-south line
(a meridian, so "distance east" is easy to reason about).
"""

import math
from decimal import Decimal
from typing import Any, cast

import psycopg
import pytest

from agent.tools.spatial import (
    MAX_ROUTE_POINTS,
    ToolInputError,
    find_hotels_near,
    find_stops_along_route,
)
from db.migrate import apply_migrations

LON = -79.0
LINE: dict[str, Any] = {"type": "LineString", "coordinates": [[LON, 37.5], [LON, 38.5]]}
NO_DB = cast(psycopg.Connection, None)  # validation fails before any query


def east(km: float, lat: float) -> float:
    """Longitude `km` east of the line at latitude `lat` (WGS84 parallel radius)."""
    a, e2 = 6378.137, 0.00669437999014
    phi = math.radians(lat)
    km_per_degree = math.pi / 180 * a * math.cos(phi) / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return LON + km / km_per_degree


def add_stop(conn: psycopg.Connection, sid: str, kind: str, lon: float, lat: float) -> None:
    conn.execute(
        "INSERT INTO stops (id, name, type, geom, overnight_ok, est_cost_usd, description, "
        "source, source_url) VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), "
        "false, 10, '', 'test_fixture', 'https://example.com/fixture')",
        (sid, f"Fixture {sid}", kind, lon, lat),
    )


def add_hotel(conn: psycopg.Connection, hid: str, price: str, lon: float, lat: float) -> None:
    conn.execute(
        "INSERT INTO hotels (id, name, brand_segment, geom, price_usd, points_per_night, "
        "amenities, source, source_url) VALUES (%s, %s, 'economy', "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, 8000, '{wifi}', 'test_fixture', "
        "'https://example.com/fixture')",
        (hid, f"Fixture {hid}", lon, lat, Decimal(price)),
    )


@pytest.fixture
def db(conn: psycopg.Connection) -> psycopg.Connection:
    apply_migrations(conn)
    return conn


# --- input validation (no database) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"types": ["casino"]}, "bad_type"),
        ({"types": []}, "bad_type"),
        ({"max_detour_km": 0}, "bad_max_detour"),
        ({"max_detour_km": 150}, "bad_max_detour"),
        ({"limit": 0}, "bad_limit"),
        ({"limit": 51}, "bad_limit"),
        ({"route": {"type": "Point", "coordinates": [LON, 38.0]}}, "bad_geometry"),
        ({"route": {"type": "LineString", "coordinates": [[LON, 38.0]]}}, "bad_geometry"),
        ({"route": [[LON, 37.5], [LON, 38.5]]}, "bad_geometry"),
        ({"route": {"type": "LineString", "coordinates": "nope"}}, "bad_geometry"),
        (
            {"route": {"type": "LineString", "coordinates": [[True, False], [LON, 38.0]]}},
            "bad_geometry",
        ),
        (
            {
                "route": {
                    "type": "LineString",
                    "coordinates": [[LON, 37.5 + i * 1e-5] for i in range(MAX_ROUTE_POINTS + 1)],
                }
            },
            "bad_geometry",
        ),
        ({"types": "winery"}, "bad_type"),
        ({"limit": 1.5}, "bad_limit"),
        ({"limit": "5"}, "bad_limit"),
        ({"limit": True}, "bad_limit"),
        ({"max_detour_km": "30"}, "bad_max_detour"),
        ({"max_detour_km": 10**400}, "bad_max_detour"),
        (
            {"route": {"type": "LineString", "coordinates": [[10**400, 38.0], [LON, 38.5]]}},
            "bad_geometry",
        ),
    ],
)
def test_find_stops_rejects_bad_input(kwargs: dict[str, Any], code: str) -> None:
    args: dict[str, Any] = {"route": LINE, "types": ["winery"], "max_detour_km": 30, "limit": 10}
    args.update(kwargs)
    with pytest.raises(ToolInputError) as err:
        find_stops_along_route(NO_DB, **args)
    assert err.value.code == code
    assert err.value.to_dict()["error"] == code


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"near": [LON]}, "bad_geometry"),
        ({"near": [200.0, 38.0]}, "bad_geometry"),
        ({"near": [True, False]}, "bad_geometry"),
        ({"near": "-79,38"}, "bad_geometry"),
        ({"radius_km": True}, "bad_radius"),
        ({"radius_km": 10**400}, "bad_radius"),
        ({"near": [LON, -(10**400)]}, "bad_geometry"),
        ({"max_price_usd": float("nan")}, "bad_price"),
        ({"max_price_usd": True}, "bad_price"),
        ({"max_price_usd": "120"}, "bad_price"),
        ({"radius_km": 0}, "bad_radius"),
        ({"radius_km": 80}, "bad_radius"),
        ({"max_price_usd": Decimal("0")}, "bad_price"),
        ({"max_price_usd": Decimal("NaN")}, "bad_price"),
        ({"limit": 0}, "bad_limit"),
    ],
)
def test_find_hotels_rejects_bad_input(kwargs: dict[str, Any], code: str) -> None:
    args: dict[str, Any] = {"near": [LON, 38.0], "radius_km": 10, "limit": 10}
    args.update(kwargs)
    with pytest.raises(ToolInputError) as err:
        find_hotels_near(NO_DB, **args)
    assert err.value.code == code


class _FakeConn:
    """Records the query parameters; returns no rows."""

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def execute(self, _sql: str, params: dict[str, Any]) -> "_FakeConn":
        self.params = params
        return self

    def fetchall(self) -> list[Any]:
        return []


@pytest.mark.parametrize("cap", [120, 120.0, Decimal("120")])
def test_find_hotels_accepts_numeric_price_caps_as_decimal(cap: Any) -> None:
    fake = _FakeConn()
    assert (
        find_hotels_near(cast(psycopg.Connection, fake), near=[LON, 38.0], max_price_usd=cap) == []
    )
    assert fake.params["max_price"] == Decimal("120")


# --- find_stops_along_route (database) -------------------------------------------------------


@pytest.mark.db
def test_stops_inside_detour_kept_outside_dropped(db: psycopg.Connection) -> None:
    add_stop(db, "stop_in", "winery", east(28, 38.0), 38.0)
    add_stop(db, "stop_out", "winery", east(32, 38.0), 38.0)
    found = find_stops_along_route(db, route=LINE, types=["winery"], max_detour_km=30)
    assert [s["id"] for s in found] == ["stop_in"]
    assert found[0]["distance_from_route_km"] == pytest.approx(28, abs=0.5)
    assert found[0]["detour_km_estimate"] == pytest.approx(56, abs=1)


@pytest.mark.db
def test_stops_ordered_by_position_along_route(db: psycopg.Connection) -> None:
    for sid, lat in [("stop_c", 38.4), ("stop_a", 37.6), ("stop_b", 38.0)]:
        add_stop(db, sid, "farm", east(5, lat), lat)
    found = find_stops_along_route(db, route=LINE, types=["farm"])
    assert [s["id"] for s in found] == ["stop_a", "stop_b", "stop_c"]
    positions = [s["position_along_route"] for s in found]
    assert positions == sorted(positions)
    assert positions[0] == pytest.approx(0.1, abs=0.01)
    assert positions[1] == pytest.approx(0.5, abs=0.01)


@pytest.mark.db
def test_stops_type_filter_limit_and_output_shape(db: psycopg.Connection) -> None:
    add_stop(db, "stop_w1", "winery", east(1, 37.7), 37.7)
    add_stop(db, "stop_w2", "winery", east(1, 37.9), 37.9)
    add_stop(db, "stop_b1", "brewery", east(1, 38.1), 38.1)
    found = find_stops_along_route(db, route=LINE, types=["winery"], limit=1)
    assert [s["id"] for s in found] == ["stop_w1"]
    stop = found[0]
    assert stop["lon"] == pytest.approx(east(1, 37.7))
    assert stop["lat"] == pytest.approx(37.7)
    assert stop["est_cost_usd"] == Decimal("10.00")
    assert stop["source_url"] == "https://example.com/fixture"
    wineries = find_stops_along_route(db, route=LINE, types=["winery"])
    assert [s["id"] for s in wineries] == ["stop_w1", "stop_w2"]  # the brewery is filtered out
    both = find_stops_along_route(db, route=LINE, types=["winery", "brewery"])
    assert [s["id"] for s in both] == ["stop_w1", "stop_w2", "stop_b1"]


@pytest.mark.db
def test_detour_cutoff_is_exact_and_inclusive(db: psycopg.Connection) -> None:
    """Uses PostGIS's own WGS84 distance, so the boundary is tested exactly, not with a margin."""
    add_stop(db, "stop_on_route", "farm", LON, 38.2)
    add_stop(db, "stop_edge", "farm", east(30, 38.0), 38.0)
    row = db.execute(
        "SELECT ST_Distance(geom::geography, ST_GeomFromText(%s, 4326)::geography) / 1000 "
        "FROM stops WHERE id = 'stop_edge'",
        (f"LINESTRING({LON} 37.5, {LON} 38.5)",),
    ).fetchone()
    assert row is not None
    edge_km = float(row[0])

    at_limit = find_stops_along_route(db, route=LINE, types=["farm"], max_detour_km=edge_km)
    assert [s["id"] for s in at_limit] == ["stop_edge", "stop_on_route"]
    assert at_limit[1]["distance_from_route_km"] == pytest.approx(0, abs=0.01)

    just_under = find_stops_along_route(
        db, route=LINE, types=["farm"], max_detour_km=edge_km - 0.001
    )
    assert [s["id"] for s in just_under] == ["stop_on_route"]


@pytest.mark.db
def test_position_is_not_rounded(db: psycopg.Connection) -> None:
    """validate_plan compares positions to catch backtracking; rounding could hide it."""
    add_stop(db, "stop_p1", "farm", east(1, 38.00001), 38.00001)
    add_stop(db, "stop_p2", "farm", east(1, 38.00004), 38.00004)
    p1, p2 = (
        s["position_along_route"] for s in find_stops_along_route(db, route=LINE, types=["farm"])
    )
    assert p1 < p2


@pytest.mark.db
@pytest.mark.parametrize(
    ("query", "index"),
    [("stops", "stops_geog_gix"), ("hotels", "hotels_geog_gix")],
)
def test_queries_can_use_the_geography_index(
    db: psycopg.Connection, query: str, index: str
) -> None:
    """With sequential scans discouraged, the planner must pick the geography expression index."""
    from agent.tools.spatial import HOTELS_SQL, STOPS_SQL

    route = '{"type": "LineString", "coordinates": [[-79.0, 37.5], [-79.0, 38.5]]}'
    db.execute("SET enable_seqscan = off")
    if query == "stops":
        params: dict[str, Any] = {"route": route, "max_m": 30000, "types": ["farm"], "limit": 5}
        plan = db.execute("EXPLAIN " + STOPS_SQL, params).fetchall()
    else:
        params = {"near": route, "radius_m": 10000, "max_price": None, "limit": 5}
        plan = db.execute("EXPLAIN " + HOTELS_SQL, params).fetchall()
    assert index in " ".join(row[0] for row in plan)


@pytest.mark.db
def test_no_stops_is_an_empty_list(db: psycopg.Connection) -> None:
    assert find_stops_along_route(db, route=LINE, types=["museum"]) == []


# --- find_hotels_near (database) -------------------------------------------------------------


@pytest.mark.db
def test_hotels_near_point_within_radius_sorted_by_distance(db: psycopg.Connection) -> None:
    add_hotel(db, "hotel_far", "90", east(8, 38.0), 38.0)
    add_hotel(db, "hotel_near", "120", east(2, 38.0), 38.0)
    add_hotel(db, "hotel_out", "60", east(12, 38.0), 38.0)
    found = find_hotels_near(db, near=[LON, 38.0], radius_km=10)
    assert [h["id"] for h in found] == ["hotel_near", "hotel_far"]
    assert found[0]["distance_km"] == pytest.approx(2, abs=0.2)
    assert found[0]["price_usd"] == Decimal("120.00")
    assert found[0]["points_per_night"] == 8000
    assert found[0]["amenities"] == ["wifi"]


@pytest.mark.db
def test_hotels_max_price_filter(db: psycopg.Connection) -> None:
    add_hotel(db, "hotel_cheap", "89.00", east(2, 38.0), 38.0)
    add_hotel(db, "hotel_pricey", "149.00", east(3, 38.0), 38.0)
    found = find_hotels_near(db, near=[LON, 38.0], radius_km=10, max_price_usd=Decimal("100"))
    assert [h["id"] for h in found] == ["hotel_cheap"]


@pytest.mark.db
def test_hotels_near_a_route_segment(db: psycopg.Connection) -> None:
    add_hotel(db, "hotel_seg", "99", east(3, 38.3), 38.3)
    add_hotel(db, "hotel_beyond", "99", east(3, 38.9), 38.9)
    found = find_hotels_near(db, near=LINE, radius_km=5)
    assert [h["id"] for h in found] == ["hotel_seg"]
