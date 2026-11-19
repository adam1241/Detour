"""calculate_trip_cost tool (spec §5.0, §5.3).

Lodging only: hotel nightly price or host fee. Points: points to pay for hotel nights;
host nights add 0. Money stays Decimal; to_dict() converts to JSON numbers at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from agent.plan.catalog import Catalog, Place


@dataclass(frozen=True)
class NightCost:
    day: int
    kind: str
    id: str
    cash_usd: Decimal
    points: int


@dataclass(frozen=True)
class TripCost:
    cash_usd: Decimal
    points: int
    hotel_nights: int
    host_nights: int
    nights: list[NightCost] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    mismatched_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_usd": float(self.cash_usd),
            "points": self.points,
            "hotel_nights": self.hotel_nights,
            "host_nights": self.host_nights,
            "nights": [
                {
                    "day": n.day,
                    "kind": n.kind,
                    "id": n.id,
                    "cash_usd": float(n.cash_usd),
                    "points": n.points,
                }
                for n in self.nights
            ],
            "unknown_ids": self.unknown_ids,
            "mismatched_ids": self.mismatched_ids,
        }


def calculate_trip_cost(
    plan: dict[str, Any], catalog: Catalog, places: dict[str, Place] | None = None
) -> TripCost:
    """Cost of a (schema-valid) draft plan's overnights, priced from the catalog.

    `places` lets validate_plan pass the snapshot it already loaded, so routing and cost use
    the same data. Overnights whose kind doesn't match their place are not priced.
    """
    overnights = [(d["day"], d["overnight"]) for d in plan["days"] if d.get("overnight")]
    if places is None:
        places = catalog.get(o["id"] for _, o in overnights)
    nights: list[NightCost] = []
    unknown: list[str] = []
    mismatched: list[str] = []
    for day, o in overnights:
        place = places.get(o["id"])
        if place is None:
            unknown.append(o["id"])
            continue
        if place.kind != ("hotel" if o["kind"] == "hotel" else "stop"):
            mismatched.append(o["id"])
            continue
        if o["kind"] == "hotel":
            cash, points = place.price_usd or Decimal("0"), place.points_per_night or 0
        else:
            cash, points = place.est_cost_usd or Decimal("0"), 0
        nights.append(NightCost(day=day, kind=o["kind"], id=o["id"], cash_usd=cash, points=points))
    return TripCost(
        cash_usd=sum((n.cash_usd for n in nights), Decimal("0")),
        points=sum(n.points for n in nights),
        hotel_nights=sum(1 for n in nights if n.kind == "hotel"),
        host_nights=sum(1 for n in nights if n.kind == "host"),
        nights=nights,
        unknown_ids=unknown,
        mismatched_ids=mismatched,
    )
