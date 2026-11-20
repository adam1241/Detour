"""Hydrate a validated plan for display (ADR-005): names, coordinates and the route line.

The plan itself carries IDs only. This runs after validation, from the catalog and the cached
corridor, so nothing displayed is invented by the model.
"""

from __future__ import annotations

from typing import Any

from agent.plan.catalog import Catalog
from agent.plan.validate import day_ends
from agent.tools.get_route import RouteError, get_route


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def hydrate(plan: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    """{plan, places: {id: {...}}, route_geojson: FeatureCollection of one line per day}."""
    ids = [s["id"] for d in plan["days"] for s in d["stops"]] + [
        d["overnight"]["id"] for d in plan["days"] if d["overnight"]
    ]
    found = catalog.get(ids)
    places = {
        pid: {
            "id": pid,
            "name": p.name,
            "kind": p.kind,
            "type": p.type,
            "lon": p.lon,
            "lat": p.lat,
            "price_usd": _num(p.price_usd),
            "points_per_night": p.points_per_night,
            "est_cost_usd": _num(p.est_cost_usd),
            "overnight_ok": p.overnight_ok,
        }
        for pid, p in found.items()
    }
    features = []
    ends = day_ends(plan, found) or []
    start: Any = plan["origin"]
    for d, end in zip(plan["days"], ends, strict=False):
        try:
            route = get_route(start, end)
        except RouteError:
            break
        features.append(
            {"type": "Feature", "properties": {"day": d["day"]}, "geometry": route["geometry"]}
        )
        start = end
    return {
        "plan": plan,
        "places": places,
        "route_geojson": {"type": "FeatureCollection", "features": features},
    }
