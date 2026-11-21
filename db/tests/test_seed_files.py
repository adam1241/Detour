from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from db.seed.load_seed import (
    FIXTURE_DIR,
    SEED_DIR,
    HotelRow,
    StopRow,
    check_sources,
    read_hotels,
    read_stops,
    validate,
)


def good_hotel(**change: Any) -> HotelRow:
    row = HotelRow(
        id="hotel_test_a",
        name="Test Hotel",
        brand_segment="economy",
        lon=-78.46,
        lat=38.66,
        price_usd=Decimal("99.00"),
        points_per_night=8000,
        amenities=("wifi",),
        source="sample_public",
        source_url="https://example.com/hotel",
    )
    return replace(row, **change)


def osm_hotel(**change: Any) -> HotelRow:
    row = good_hotel(
        id="hotel_osm_123",
        price_usd=None,
        points_per_night=None,
        source="osm",
        source_url="https://www.openstreetmap.org/node/123",
        source_ref="osm:node/123",
        licence="ODbL-1.0",
    )
    return replace(row, **change)


def ridb_stop(**change: Any) -> StopRow:
    row = good_stop(
        id="stop_ridb_456",
        overnight_ok=True,
        est_cost_usd=None,
        source="ridb",
        source_url="https://www.recreation.gov/camping/campgrounds/456",
        source_ref="ridb:facility/456",
        licence="US-PD",
    )
    return replace(row, **change)


def good_stop(**change: Any) -> StopRow:
    row = StopRow(
        id="stop_test_a",
        name="Test Farm",
        type="farm",
        lon=-79.07,
        lat=38.15,
        overnight_ok=False,
        est_cost_usd=Decimal("0"),
        description="",
        source="sample_public",
        source_url="https://example.com/farm",
    )
    return replace(row, **change)


def test_seed_csvs_are_valid_sample_public_data() -> None:
    hotels = read_hotels(SEED_DIR / "hotels.csv")
    stops = read_stops(SEED_DIR / "stops.csv")
    assert validate(hotels, stops) == []
    check_sources(hotels, stops, allow_fixture=False)


def test_fixture_is_valid_and_labelled_as_fixture() -> None:
    hotels = read_hotels(FIXTURE_DIR / "hotels.csv")
    stops = read_stops(FIXTURE_DIR / "stops.csv")
    assert len(hotels) + len(stops) == 5
    assert validate(hotels, stops) == []
    assert {h.source for h in hotels} | {s.source for s in stops} == {"test_fixture"}


def test_fixture_rows_refused_outside_tests() -> None:
    hotels = read_hotels(FIXTURE_DIR / "hotels.csv")
    with pytest.raises(ValueError, match="test_fixture"):
        check_sources(hotels, [], allow_fixture=False)
    check_sources(hotels, [], allow_fixture=True)


def test_good_rows_pass() -> None:
    assert validate([good_hotel()], [good_stop()]) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": "h_1"}, "id must match"),
        ({"brand_segment": "luxury"}, "brand_segment"),
        ({"lon": -150.0, "lat": 61.2}, "outside the contiguous US"),  # Anchorage
        ({"lat": 21.3, "lon": -157.8}, "outside the contiguous US"),  # Honolulu
        ({"price_usd": Decimal("0")}, "price_usd"),
        ({"points_per_night": -1}, "points_per_night"),
        ({"source": "scraped"}, "source"),
        ({"source_url": "http://example.com"}, "source_url"),
        ({"source_url": "https://"}, "source_url"),
        ({"source_url": "https:///path"}, "source_url"),
        ({"name": " "}, "name"),
        ({"name": "\t\n"}, "name"),
    ],
)
def test_bad_hotel_rejected(change: dict[str, Any], message: str) -> None:
    errors = validate([good_hotel(**change)], [])
    assert any(message in e for e in errors), errors


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": "hotel_x"}, "id must match"),
        ({"type": "casino"}, "type"),
        ({"est_cost_usd": Decimal("-1")}, "est_cost_usd"),
        ({"lat": 19.4, "lon": -99.1}, "outside the contiguous US"),  # Mexico City
    ],
)
def test_bad_stop_rejected(change: dict[str, Any], message: str) -> None:
    errors = validate([], [good_stop(**change)])
    assert any(message in e for e in errors), errors


@pytest.mark.parametrize("price", ["0.01", "129.50", "999999.99"])
def test_hotel_prices_that_fit_numeric_8_2_pass(price: str) -> None:
    assert validate([good_hotel(price_usd=Decimal(price))], []) == []


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", "0.004", "10.005", "1000000"])
def test_hotel_prices_not_finite_or_not_numeric_8_2_rejected(price: str) -> None:
    errors = validate([good_hotel(price_usd=Decimal(price))], [])
    assert any("price_usd" in e for e in errors), errors


@pytest.mark.parametrize("cost", ["NaN", "Infinity", "0.001", "1000000"])
def test_stop_costs_not_finite_or_not_numeric_8_2_rejected(cost: str) -> None:
    errors = validate([], [good_stop(est_cost_usd=Decimal(cost))])
    assert any("est_cost_usd" in e for e in errors), errors


def test_duplicate_ids_rejected() -> None:
    errors = validate([good_hotel(), good_hotel()], [])
    assert any("duplicate id" in e for e in errors), errors


def test_places_anywhere_in_the_contiguous_us_pass() -> None:
    denver = good_hotel(lon=-104.99, lat=39.74)
    seattle = good_stop(lon=-122.33, lat=47.61)
    assert validate([denver], [seattle]) == []


def test_osm_and_ridb_rows_without_prices_pass() -> None:
    assert validate([osm_hotel()], [ridb_stop()]) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"price_usd": Decimal("90.00")}, "prices only come from a fetched page"),
        ({"points_per_night": 8000}, "prices only come from a fetched page"),
        ({"source_ref": None}, "source_ref"),
        ({"source_ref": "osm:node/abc"}, "source_ref"),
        ({"licence": None}, "licence"),
        ({"licence": "CC-BY"}, "licence"),
    ],
)
def test_bad_osm_hotel_rejected(change: dict[str, Any], message: str) -> None:
    errors = validate([osm_hotel(**change)], [])
    assert any(message in e for e in errors), errors


def test_ridb_stop_with_a_cost_is_rejected() -> None:
    errors = validate([], [ridb_stop(est_cost_usd=Decimal("20"))])
    assert any("prices only come from a fetched page" in e for e in errors), errors


@pytest.mark.parametrize("field", ["price_usd", "points_per_night"])
def test_hand_sourced_hotels_still_need_prices(field: str) -> None:
    errors = validate([good_hotel(**{field: None})], [])
    assert any(field in e and "required" in e for e in errors), errors


def test_hand_sourced_stop_still_needs_a_cost() -> None:
    errors = validate([], [good_stop(est_cost_usd=None)])
    assert any("est_cost_usd" in e and "required" in e for e in errors), errors


def test_csvs_with_source_ref_and_licence_columns_are_read(tmp_path: Any) -> None:
    from db.seed.load_seed import HOTEL_COLUMNS, NATIONWIDE_COLUMNS

    path = tmp_path / "hotels.csv"
    header = ",".join([*HOTEL_COLUMNS, *NATIONWIDE_COLUMNS])
    path.write_text(
        header + "\nhotel_osm_123,Test Hotel,economy,-104.99,39.74,,,,osm,"
        "https://www.openstreetmap.org/node/123,osm:node/123,ODbL-1.0\n",
        encoding="utf-8",
    )
    (row,) = read_hotels(path)
    assert row.price_usd is None and row.points_per_night is None
    assert row.source_ref == "osm:node/123" and row.licence == "ODbL-1.0"
    assert validate([row], []) == []
