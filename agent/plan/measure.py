"""Fill a draft plan's numbers from the tools (spec §5.1: never invent distances or prices).

The model drafts the structure (days, stops, overnights, narratives). Drive time and distance
per day come from get_route plus side-trip estimates, and totals from calculate_trip_cost:
the same functions validate_plan checks against.
"""

from __future__ import annotations

from typing import Any

from agent.plan.catalog import Catalog
from agent.plan.cost import calculate_trip_cost
from agent.plan.validate import day_ends, measure_leg, schema_violations, stop_points
from agent.tools.get_route import RouteError


def complete_draft(plan: Any) -> Any:
    """Add fields the model may leave out, so a near-complete draft can be checked."""
    if not isinstance(plan, dict):
        return plan
    plan.setdefault("warnings", [])
    plan.setdefault("alternatives", [])
    plan.setdefault("totals", {"cash_usd": 0, "points": 0, "hotel_nights": 0, "host_nights": 0})
    for d in plan.get("days") or []:
        if isinstance(d, dict):
            d.setdefault("drive_minutes", 0)
            d.setdefault("distance_km", 0)
            d.setdefault("stops", [])
    return plan


def fill_numbers(plan: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    """Set each day's drive_minutes and distance_km and the plan totals, in place.

    Leaves numbers untouched where they can't be computed (schema errors, unknown places,
    off-corridor ends): validate_plan reports those.
    """
    if schema_violations(plan):
        return plan
    ids = [s["id"] for d in plan["days"] for s in d["stops"]] + [
        d["overnight"]["id"] for d in plan["days"] if d["overnight"]
    ]
    places = catalog.get(ids)
    ends = day_ends(plan, places)
    if ends is not None:
        start: Any = plan["origin"]
        for d, end in zip(plan["days"], ends, strict=True):
            try:
                leg = measure_leg(start, end, stop_points(d, places))
            except RouteError:
                break
            d["drive_minutes"] = round(leg.minutes, 1)
            d["distance_km"] = round(leg.km, 1)
            start = end
    cost = calculate_trip_cost(plan, catalog, places)
    if not cost.unknown_ids and not cost.mismatched_ids:
        plan["totals"] = {
            "cash_usd": float(cost.cash_usd),
            "points": cost.points,
            "hotel_nights": cost.hotel_nights,
            "host_nights": cost.host_nights,
        }
    return plan
