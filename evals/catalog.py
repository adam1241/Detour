"""An in-memory catalog of the real seed rows, so evals and tests run without a database."""

from __future__ import annotations

from agent.plan.catalog import InMemoryCatalog, Place
from db.seed.load_seed import SEED_DIR, read_hotels, read_stops


def seed_catalog() -> InMemoryCatalog:
    hotels = [
        Place(
            id=h.id,
            kind="hotel",
            name=h.name,
            lon=h.lon,
            lat=h.lat,
            price_usd=h.price_usd,
            points_per_night=h.points_per_night,
        )
        for h in read_hotels(SEED_DIR / "hotels.csv")
    ]
    stops = [
        Place(
            id=s.id,
            kind="stop",
            name=s.name,
            lon=s.lon,
            lat=s.lat,
            type=s.type,
            est_cost_usd=s.est_cost_usd,
            overnight_ok=s.overnight_ok,
        )
        for s in read_stops(SEED_DIR / "stops.csv")
    ]
    return InMemoryCatalog([*hotels, *stops])
