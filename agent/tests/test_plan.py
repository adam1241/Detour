"""calculate_trip_cost and validate_plan (spec §5.1-5.3), against an in-memory catalog.

Test places sit on real corridor town coordinates; drive times come from get_route itself,
so the base plan is valid and each test breaks exactly one rule.
"""

import copy
from decimal import Decimal
from typing import Any

import pytest

from agent.plan.catalog import InMemoryCatalog, Place
from agent.plan.cost import calculate_trip_cost
from agent.plan.validate import blocking, validate_plan
from agent.tools.get_route import CORRIDOR_TOWNS, get_route

T = CORRIDOR_TOWNS


def hotel(pid: str, town: str, price: str, points: int) -> Place:
    lon, lat = T[town]
    return Place(
        id=pid,
        kind="hotel",
        name=pid,
        lon=lon,
        lat=lat,
        price_usd=Decimal(price),
        points_per_night=points,
    )


def stop(pid: str, town: str, kind: str, cost: str, overnight_ok: bool = False) -> Place:
    lon, lat = T[town]
    return Place(
        id=pid,
        kind="stop",
        name=pid,
        lon=lon,
        lat=lat,
        type=kind,
        est_cost_usd=Decimal(cost),
        overnight_ok=overnight_ok,
    )


CATALOG = InMemoryCatalog(
    [
        hotel("hotel_luray", "Luray, VA", "80.00", 10000),
        hotel("hotel_roanoke", "Roanoke, VA", "90.00", 12000),
        hotel("hotel_boone", "Boone, NC", "120.00", 15000),
        stop("stop_museum_staunton", "Staunton, VA", "museum", "12.00"),
        stop("stop_farm_roanoke", "Roanoke, VA", "farm", "45.00", overnight_ok=True),
        stop("stop_museum_boone", "Boone, NC", "museum", "8.00"),
    ]
)


def leg(start: Any, end: Any) -> tuple[float, float]:
    r = get_route(start, end)
    return r["duration_min"], r["distance_km"]


def day(
    n: int, start: Any, end: Any, overnight: dict[str, str] | None, stops: Any = ()
) -> dict[str, Any]:
    minutes, km = leg(start, end)
    return {
        "day": n,
        "drive_minutes": minutes,
        "distance_km": km,
        "stops": [{"id": s, "type": t, "reason": "On the way."} for s, t in stops],
        "overnight": overnight,
        "narrative": "A good day on the road.",
    }


def base_plan() -> dict[str, Any]:
    luray, roanoke, boone = T["Luray, VA"], T["Roanoke, VA"], T["Boone, NC"]
    return {
        "trip_id": "trip_test",
        "summary": "DC to Asheville in four days.",
        "origin": "Washington, DC",
        "destination": "Asheville, NC",
        "vehicle": "car",
        "days": [
            day(
                1,
                "Washington, DC",
                luray,
                {"kind": "hotel", "id": "hotel_luray", "reason": "Short first day."},
            ),
            day(
                2,
                luray,
                roanoke,
                {"kind": "hotel", "id": "hotel_roanoke", "reason": "Halfway."},
                stops=[("stop_museum_staunton", "museum")],
            ),
            day(
                3,
                roanoke,
                boone,
                {"kind": "hotel", "id": "hotel_boone", "reason": "Mountains."},
                stops=[("stop_museum_boone", "museum")],
            ),
            day(4, boone, "Asheville, NC", None),
        ],
        "totals": {"cash_usd": 290.00, "points": 37000, "hotel_nights": 3, "host_nights": 0},
        "warnings": [],
        "alternatives": [],
    }


def codes(plan: dict[str, Any], **kwargs: Any) -> list[str]:
    return [v.code for v in validate_plan(plan, CATALOG, **kwargs)]


# --- calculate_trip_cost -------------------------------------------------------------------


def test_cost_is_lodging_only_cash_and_hotel_points() -> None:
    cost = calculate_trip_cost(base_plan(), CATALOG)
    assert cost.cash_usd == Decimal("290.00")
    assert cost.points == 37000
    assert (cost.hotel_nights, cost.host_nights) == (3, 0)
    assert [(n.day, n.id, n.cash_usd, n.points) for n in cost.nights] == [
        (1, "hotel_luray", Decimal("80.00"), 10000),
        (2, "hotel_roanoke", Decimal("90.00"), 12000),
        (3, "hotel_boone", Decimal("120.00"), 15000),
    ]


def test_host_night_costs_its_fee_and_no_points() -> None:
    plan = base_plan()
    plan["vehicle"] = "rv"
    plan["days"][1]["overnight"] = {
        "kind": "host",
        "id": "stop_farm_roanoke",
        "reason": "Farm stay.",
    }
    cost = calculate_trip_cost(plan, CATALOG)
    assert cost.cash_usd == Decimal("245.00")  # 80 + 45 + 120
    assert cost.points == 25000
    assert (cost.hotel_nights, cost.host_nights) == (2, 1)


def test_cost_to_dict_uses_json_numbers() -> None:
    payload = calculate_trip_cost(base_plan(), CATALOG).to_dict()
    assert payload["cash_usd"] == 290.0
    assert payload["nights"][0] == {
        "day": 1,
        "kind": "hotel",
        "id": "hotel_luray",
        "cash_usd": 80.0,
        "points": 10000,
    }


# --- validate_plan: valid plans -------------------------------------------------------------


def test_base_plan_is_valid() -> None:
    assert validate_plan(base_plan(), CATALOG) == []


def test_rv_host_overnight_is_valid() -> None:
    plan = base_plan()
    plan["vehicle"] = "rv"
    plan["days"][1]["overnight"] = {
        "kind": "host",
        "id": "stop_farm_roanoke",
        "reason": "Farm stay.",
    }
    plan["totals"] = {"cash_usd": 245.0, "points": 25000, "hotel_nights": 2, "host_nights": 1}
    assert validate_plan(plan, CATALOG) == []


# --- validate_plan: one broken rule each ----------------------------------------------------


def test_schema_errors_stop_further_checks() -> None:
    plan = base_plan()
    del plan["totals"]
    plan["days"][0]["overnight"]["id"] = "Hotel Luray"
    found = codes(plan)
    assert found and set(found) == {"schema"}


def test_unknown_ids_are_not_grounded() -> None:
    plan = base_plan()
    plan["days"][1]["stops"][0]["id"] = "stop_invented_winery"
    assert "unknown_id" in codes(plan)


def test_overnight_kind_must_match_the_place() -> None:
    plan = base_plan()
    plan["days"][0]["overnight"] = {"kind": "hotel", "id": "stop_museum_staunton", "reason": "x"}
    assert "kind_mismatch" in codes(plan)


def test_host_overnight_needs_an_overnight_host() -> None:
    plan = base_plan()
    plan["vehicle"] = "rv"
    plan["days"][2]["overnight"] = {"kind": "host", "id": "stop_museum_boone", "reason": "x"}
    assert "not_overnight_host" in codes(plan)


def test_car_trips_cannot_use_host_overnights() -> None:
    plan = base_plan()
    plan["days"][1]["overnight"] = {
        "kind": "host",
        "id": "stop_farm_roanoke",
        "reason": "Farm stay.",
    }
    plan["totals"] = {"cash_usd": 245.0, "points": 25000, "hotel_nights": 2, "host_nights": 1}
    assert codes(plan) == ["vehicle_rule"]


def test_stop_type_must_match_the_place() -> None:
    plan = base_plan()
    plan["days"][1]["stops"][0]["type"] = "winery"
    assert "type_mismatch" in codes(plan)


def test_n_days_means_n_minus_one_overnights() -> None:
    plan = base_plan()
    plan["days"][1]["overnight"] = None
    assert "overnight_missing" in codes(plan)
    plan = base_plan()
    plan["days"][3]["overnight"] = {"kind": "hotel", "id": "hotel_boone", "reason": "x"}
    assert "last_day_overnight" in codes(plan)


def test_days_are_numbered_from_one() -> None:
    plan = base_plan()
    plan["days"][2]["day"] = 5
    assert "day_numbers" in codes(plan)


def test_invented_drive_time_or_distance_is_caught() -> None:
    plan = base_plan()
    plan["days"][0]["drive_minutes"] = 60
    plan["days"][2]["distance_km"] = 100
    assert set(codes(plan)) == {"drive_mismatch", "distance_mismatch"}


def test_small_rounding_differences_are_tolerated() -> None:
    plan = base_plan()
    plan["days"][0]["drive_minutes"] = round(plan["days"][0]["drive_minutes"])
    plan["days"][0]["distance_km"] = round(plan["days"][0]["distance_km"])
    assert validate_plan(plan, CATALOG) == []


def test_max_drive_per_day_is_a_hard_rule() -> None:
    found = validate_plan(base_plan(), CATALOG, max_drive_minutes=150)
    assert {v.code for v in blocking(found)} == {"max_drive"}
    assert {v.day for v in found} == {2, 3}


def test_broken_constraint_is_acknowledged_only_with_warning_and_alternative() -> None:
    plan = base_plan()
    plan["warnings"] = ["Day 3 is 3h 30m of driving, over your 2h 30m limit."]
    assert blocking(validate_plan(plan, CATALOG, max_drive_minutes=150))  # no alternative yet
    plan["alternatives"] = ["Add a fifth day with a night in Wytheville."]
    found = validate_plan(plan, CATALOG, max_drive_minutes=150)
    assert found and all(v.acknowledged for v in found)
    assert blocking(found) == []


def test_budget_is_lodging_only_and_hard() -> None:
    assert codes(base_plan(), budget_usd=Decimal("290")) == []
    assert codes(base_plan(), budget_usd=Decimal("289.99")) == ["budget"]


def test_totals_must_match_the_cost_tool() -> None:
    plan = base_plan()
    plan["totals"]["cash_usd"] = 250.0
    plan["totals"]["hotel_nights"] = 2
    assert codes(plan) == ["totals_mismatch"]


def test_overnights_out_of_route_order_are_backtracking() -> None:
    plan = base_plan()
    roanoke, boone = T["Roanoke, VA"], T["Boone, NC"]
    plan["days"][1] = day(
        2, T["Luray, VA"], boone, {"kind": "hotel", "id": "hotel_boone", "reason": "x"}
    )
    plan["days"][2] = day(
        3, boone, roanoke, {"kind": "hotel", "id": "hotel_roanoke", "reason": "x"}
    )
    plan["days"][3] = day(4, roanoke, "Asheville, NC", None)
    assert "backtracking" in codes(plan)


def test_stop_behind_the_previous_overnight_is_backtracking() -> None:
    plan = base_plan()
    plan["days"][2]["stops"] = [{"id": "stop_museum_staunton", "type": "museum", "reason": "x"}]
    assert "backtracking" in codes(plan)


def test_places_off_the_corridor_are_route_errors() -> None:
    plan = base_plan()
    plan["origin"] = "Richmond, VA"
    assert "route_error" in codes(plan)


def test_replan_keeps_earlier_days_identical() -> None:
    previous = base_plan()
    replan = copy.deepcopy(previous)
    replan["days"][2]["narrative"] = "A slower, shorter day."
    assert codes(replan, previous_plan=previous, replan_from_day=3) == []
    replan["days"][0]["narrative"] = "Changed."
    found = validate_plan(replan, CATALOG, previous_plan=previous, replan_from_day=3)
    assert [(v.code, v.day) for v in found] == [("replan_changed_earlier_day", 1)]


def test_violation_to_dict_for_the_agent() -> None:
    plan = base_plan()
    plan["days"][0]["drive_minutes"] = 60
    payload = validate_plan(plan, CATALOG)[0].to_dict()
    assert payload["code"] == "drive_mismatch"
    assert payload["day"] == 1
    assert "get_route" in payload["message"]


@pytest.mark.parametrize("bad", [None, [], "plan"])
def test_non_object_plans_are_schema_errors(bad: Any) -> None:
    assert [v.code for v in validate_plan(bad, CATALOG)] == ["schema"]


@pytest.mark.db
def test_db_catalog_reads_hotels_and_stops(conn: Any) -> None:
    from agent.plan.catalog import DbCatalog
    from db.migrate import apply_migrations
    from db.seed.load_seed import FIXTURE_DIR, load, read_hotels, read_stops

    apply_migrations(conn)
    load(
        conn,
        read_hotels(FIXTURE_DIR / "hotels.csv"),
        read_stops(FIXTURE_DIR / "stops.csv"),
        allow_fixture=True,
    )
    found = DbCatalog(conn).get(["hotel_fixture_a", "stop_fixture_farm", "hotel_missing"])
    assert set(found) == {"hotel_fixture_a", "stop_fixture_farm"}
    h, s = found["hotel_fixture_a"], found["stop_fixture_farm"]
    assert (h.kind, h.price_usd, h.points_per_night) == ("hotel", Decimal("89.00"), 8000)
    assert (s.kind, s.type, s.est_cost_usd, s.overnight_ok) == (
        "stop",
        "farm",
        Decimal("25.00"),
        True,
    )
    assert h.point == pytest.approx([-78.46, 38.66])
