"""OpenStreetMap and RIDB importers (ADR-008): pure row-building logic, no network."""

from __future__ import annotations

from typing import Any

from db.seed.import_osm import BRAND_SEGMENTS, hotel_rows, overpass_query, stop_rows
from db.seed.import_ridb import campground_rows
from db.seed.load_seed import validate

DENVER = {"lat": 39.74, "lon": -104.99}


def node(osm_id: int, tags: dict[str, str], **point: float) -> dict[str, Any]:
    return {"type": "node", "id": osm_id, **(point or DENVER), "tags": tags}


def way(osm_id: int, tags: dict[str, str]) -> dict[str, Any]:
    return {"type": "way", "id": osm_id, "center": DENVER, "tags": tags}


def test_choice_brand_hotels_become_unpriced_osm_rows() -> None:
    (row,) = hotel_rows(
        [node(123, {"tourism": "hotel", "brand": "Comfort Inn", "name": "Comfort Inn Denver"})]
    )
    assert row.id == "hotel_osm_node_123"
    assert row.name == "Comfort Inn Denver"
    assert row.brand_segment == BRAND_SEGMENTS["Comfort Inn"]
    assert row.price_usd is None and row.points_per_night is None
    assert row.source == "osm" and row.source_ref == "osm:node/123"
    assert row.source_url == "https://www.openstreetmap.org/node/123"
    assert row.licence == "ODbL-1.0"
    assert validate([row], []) == []


def test_ways_use_their_center_point() -> None:
    (row,) = hotel_rows([way(9, {"tourism": "motel", "brand": "Econo Lodge"})])
    assert (row.lon, row.lat) == (DENVER["lon"], DENVER["lat"])
    assert row.name == "Econo Lodge"  # no name tag: the brand is the name


def test_other_brands_unnamed_and_out_of_area_places_are_skipped() -> None:
    rows = hotel_rows(
        [
            node(1, {"tourism": "hotel", "brand": "Hilton"}),
            node(2, {"tourism": "hotel", "brand": "Sleep Inn"}, lat=61.2, lon=-150.0),  # Alaska
            {
                "type": "way",
                "id": 3,
                "tags": {"tourism": "hotel", "brand": "Sleep Inn"},
            },  # no point
        ]
    )
    assert rows == []


def test_duplicate_elements_are_loaded_once() -> None:
    tags = {"tourism": "hotel", "brand": "Quality Inn"}
    assert len(hotel_rows([node(5, tags), node(5, tags)])) == 1


def test_stop_types_come_from_osm_tags() -> None:
    rows = stop_rows(
        [
            node(1, {"craft": "winery", "name": "A Winery"}),
            node(2, {"craft": "brewery", "name": "A Brewery"}),
            way(3, {"tourism": "museum", "name": "A Museum"}),
            node(4, {"shop": "farm", "name": "A Farm"}),
            node(5, {"craft": "winery"}),  # unnamed: skipped
            node(6, {"amenity": "cafe", "name": "Not a stop"}),
        ]
    )
    assert [(r.id, r.type) for r in rows] == [
        ("stop_osm_node_1", "winery"),
        ("stop_osm_node_2", "brewery"),
        ("stop_osm_way_3", "museum"),
        ("stop_osm_node_4", "farm"),
    ]
    assert all(r.overnight_ok is False and r.est_cost_usd is None for r in rows)
    assert validate([], rows) == []


def test_overpass_query_targets_one_state_and_asks_for_centers() -> None:
    query = overpass_query("CO", "hotels")
    assert '"ISO3166-2"="US-CO"' in query
    assert "out center tags" in query
    assert "Comfort Inn" in query and "tourism" in query


def facility(fid: str, **change: Any) -> dict[str, Any]:
    rec = {
        "FacilityID": fid,
        "FacilityName": "UNION CREEK CAMPGROUND",
        "FacilityTypeDescription": "Campground",
        "FacilityDescription": "<p>Sites for tents and RVs up to 30 feet.</p>",
        "Keywords": "",
        "FacilityLatitude": 44.68,
        "FacilityLongitude": -118.0,
        "Enabled": True,
    }
    rec.update(change)
    return rec


def test_ridb_campgrounds_with_rv_sites_become_overnight_stops() -> None:
    (row,) = campground_rows([facility("233115")])
    assert row.id == "stop_ridb_233115"
    assert row.type == "campground" and row.overnight_ok is True
    assert row.name == "Union Creek Campground"
    assert row.est_cost_usd is None
    assert row.source == "ridb" and row.source_ref == "ridb:facility/233115"
    assert row.source_url == "https://www.recreation.gov/camping/campgrounds/233115"
    assert row.licence == "US-PD"
    assert validate([], [row]) == []


def test_ridb_skips_non_campgrounds_no_rv_mention_bad_points_and_disabled() -> None:
    rows = campground_rows(
        [
            facility("1", FacilityTypeDescription="Trailhead"),
            facility("2", FacilityDescription="Walk-in tent sites only.", Keywords="tent"),
            facility("3", FacilityLatitude=0, FacilityLongitude=0),
            facility("4", Enabled=False),
            facility("5", FacilityLatitude=21.3, FacilityLongitude=-157.8),  # Hawaii
            facility("abc"),  # not a numeric RIDB id
        ]
    )
    assert rows == []


def test_ridb_rv_mention_can_come_from_keywords() -> None:
    (row,) = campground_rows(
        [facility("7", FacilityDescription="Tent sites.", Keywords="RV,camping")]
    )
    assert row.id == "stop_ridb_7"
