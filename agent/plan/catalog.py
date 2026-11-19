"""Place lookup for plan cost and validation: the database in production, memory in tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

import psycopg


@dataclass(frozen=True)
class Place:
    id: str
    kind: Literal["hotel", "stop"]
    name: str
    lon: float
    lat: float
    price_usd: Decimal | None = None  # hotels
    points_per_night: int | None = None  # hotels
    type: str | None = None  # stops
    est_cost_usd: Decimal | None = None  # stops
    overnight_ok: bool | None = None  # stops

    @property
    def point(self) -> list[float]:
        return [self.lon, self.lat]


class CatalogError(RuntimeError):
    """The place catalog couldn't be read (e.g. database down). Retryable at the tool boundary."""

    def to_dict(self) -> dict[str, object]:
        return {
            "error": "catalog_unavailable",
            "retryable": True,
            "message": "place catalog is unavailable; retry",
        }


class Catalog(Protocol):
    def get(self, ids: Iterable[str]) -> dict[str, Place]:
        """The places that exist among `ids` (unknown ids are simply absent)."""
        ...


class InMemoryCatalog:
    def __init__(self, places: Iterable[Place]) -> None:
        self._places = {p.id: p for p in places}

    def get(self, ids: Iterable[str]) -> dict[str, Place]:
        return {i: self._places[i] for i in ids if i in self._places}

    def all(self) -> list[Place]:
        return list(self._places.values())


HOTELS_SQL = """
SELECT id, name, ST_X(geom), ST_Y(geom), price_usd, points_per_night
FROM hotels WHERE id = ANY(%s)
"""
STOPS_SQL = """
SELECT id, name, ST_X(geom), ST_Y(geom), type, est_cost_usd, overnight_ok
FROM stops WHERE id = ANY(%s)
"""


class DbCatalog:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self, ids: Iterable[str]) -> dict[str, Place]:
        try:
            return self._get(sorted(set(ids)))
        except psycopg.Error as err:
            raise CatalogError(str(err)) from err

    def _get(self, wanted: list[str]) -> dict[str, Place]:
        found: dict[str, Place] = {}
        for r in self._conn.execute(HOTELS_SQL, (wanted,)).fetchall():
            found[r[0]] = Place(
                id=r[0],
                kind="hotel",
                name=r[1],
                lon=r[2],
                lat=r[3],
                price_usd=r[4],
                points_per_night=r[5],
            )
        for r in self._conn.execute(STOPS_SQL, (wanted,)).fetchall():
            found[r[0]] = Place(
                id=r[0],
                kind="stop",
                name=r[1],
                lon=r[2],
                lat=r[3],
                type=r[4],
                est_cost_usd=r[5],
                overnight_ok=r[6],
            )
        return found
