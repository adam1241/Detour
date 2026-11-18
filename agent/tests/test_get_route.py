"""get_route reads the cached corridor (db/seed/corridor_route.geojson); no network."""

import math

import pytest

from agent.tools.get_route import CORRIDOR_TOWNS, RouteError, get_route


def km_between(a: list[float], b: list[float]) -> float:
    """Great-circle distance between two [lon, lat] points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def town_point(name: str) -> list[float]:
    return CORRIDOR_TOWNS[name]


def test_full_corridor_shape_and_totals() -> None:
    route = get_route("Washington, DC", "Asheville, NC")
    assert route["origin"] == "Washington, DC"
    assert route["destination"] == "Asheville, NC"
    assert route["geometry"]["type"] == "LineString"
    assert len(route["legs"]) == 6
    assert route["distance_km"] == pytest.approx(sum(leg["distance_km"] for leg in route["legs"]))
    assert route["duration_min"] == pytest.approx(sum(leg["duration_min"] for leg in route["legs"]))
    # Real road distance via the six towns: well above the ~550 km straight line, below 1200 km.
    assert 650 < route["distance_km"] < 1200
    assert 8 * 60 < route["duration_min"] < 16 * 60


def test_coordinates_are_lon_lat_inside_the_corridor() -> None:
    coords = get_route("Washington, DC", "Asheville, NC")["geometry"]["coordinates"]
    assert len(coords) > 100
    for lon, lat in coords:
        assert -83.0 <= lon <= -76.8
        assert 35.3 <= lat <= 39.2


def test_route_starts_and_ends_near_the_towns() -> None:
    coords = get_route("Luray", "Roanoke")["geometry"]["coordinates"]
    assert km_between(coords[0], town_point("Luray, VA")) < 5
    assert km_between(coords[-1], town_point("Roanoke, VA")) < 5


def test_sub_route_uses_only_its_legs() -> None:
    full = get_route("Washington, DC", "Asheville, NC")
    part = get_route("Luray, VA", "Roanoke, VA")
    assert [(leg["from"], leg["to"]) for leg in part["legs"]] == [
        ("Luray, VA", "Staunton, VA"),
        ("Staunton, VA", "Roanoke, VA"),
    ]
    assert part["distance_km"] < full["distance_km"]


def test_reverse_direction() -> None:
    north = get_route("Asheville, NC", "Washington, DC")
    south = get_route("Washington, DC", "Asheville, NC")
    assert north["distance_km"] == pytest.approx(south["distance_km"])
    assert north["legs"][0]["from"] == "Asheville, NC"
    assert north["legs"][-1]["to"] == "Washington, DC"
    assert km_between(north["geometry"]["coordinates"][0], town_point("Asheville, NC")) < 5


@pytest.mark.parametrize(
    "name", ["Washington, D.C.", "washington dc", "DC", "Washington", "District of Columbia"]
)
def test_dc_aliases(name: str) -> None:
    assert get_route(name, "Front Royal")["origin"] == "Washington, DC"


def test_state_suffixes_accepted() -> None:
    route = get_route("Boone, North Carolina", "asheville nc")
    assert (route["origin"], route["destination"]) == ("Boone, NC", "Asheville, NC")


@pytest.mark.parametrize("place", ["Richmond, VA", "Charlotte", ""])
def test_places_off_the_corridor_rejected(place: str) -> None:
    with pytest.raises(RouteError, match="outside v1 coverage"):
        get_route(place, "Asheville, NC")


def test_same_origin_and_destination_rejected() -> None:
    with pytest.raises(RouteError, match="same place"):
        get_route("Luray", "Luray, VA")


def test_waypoints_between_origin_and_destination_accepted() -> None:
    route = get_route("Washington, DC", "Roanoke, VA", waypoints=["Luray", "Staunton"])
    assert route["destination"] == "Roanoke, VA"


@pytest.mark.parametrize("waypoints", [["Boone"], ["Staunton", "Luray"]])
def test_waypoints_off_segment_or_out_of_order_rejected(waypoints: list[str]) -> None:
    with pytest.raises(RouteError, match="waypoint"):
        get_route("Washington, DC", "Roanoke, VA", waypoints=waypoints)
