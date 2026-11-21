"""The planner's five tools as Converse tool specs, and the dispatcher that runs them.

Adapters keep the model's inputs small: it names corridor towns or [lon, lat] points, and route
geometry is built here (a LineString of thousands of points never passes through the model).
Every place ID a search returns is recorded in `allowed_ids` for the grounding check.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import time
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, Protocol

import psycopg

from agent.plan.catalog import Catalog, CatalogError, Place
from agent.plan.cost import calculate_trip_cost
from agent.plan.measure import complete_draft, fill_numbers
from agent.plan.validate import blocking, day_ends, schema_violations, validate_plan
from agent.request import TripRequest
from agent.tools.get_route import CORRIDOR_TOWNS, RouteError, canonical_town, get_route
from agent.tools.spatial import (
    STOP_TYPES,
    ToolInputError,
    find_hotels_near,
    find_stops_along_route,
)


class Spatial(Protocol):
    def stops_along(
        self, route: dict[str, Any], types: Sequence[str], max_detour_km: float, limit: int
    ) -> list[dict[str, Any]]: ...

    def hotels_near(
        self, near: Any, radius_km: float, max_price_usd: Any, limit: int
    ) -> list[dict[str, Any]]: ...


class DbSpatial:
    """PostGIS search (ADR-003)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def stops_along(
        self, route: dict[str, Any], types: Sequence[str], max_detour_km: float, limit: int
    ) -> list[dict[str, Any]]:
        try:
            return find_stops_along_route(
                self.conn, route=route, types=types, max_detour_km=max_detour_km, limit=limit
            )
        except psycopg.Error as err:
            raise CatalogError(str(err)) from err

    def hotels_near(
        self, near: Any, radius_km: float, max_price_usd: Any, limit: int
    ) -> list[dict[str, Any]]:
        try:
            return find_hotels_near(
                self.conn, near=near, radius_km=radius_km, max_price_usd=max_price_usd, limit=limit
            )
        except psycopg.Error as err:
            raise CatalogError(str(err)) from err


def _km(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


class MemorySpatial:
    """The same searches over in-memory places (seed CSVs): evals and tests without PostGIS.
    Distances are to the nearest route vertex, close to PostGIS for the dense OSRM line."""

    def __init__(self, places: Iterable[Place]) -> None:
        self.places = list(places)

    def stops_along(
        self, route: dict[str, Any], types: Sequence[str], max_detour_km: float, limit: int
    ) -> list[dict[str, Any]]:
        if not types or any(t not in STOP_TYPES for t in types):
            raise ToolInputError("bad_type", f"types must be a non-empty list from {STOP_TYPES}")
        coords = route["coordinates"]
        cum = [0.0]
        for p, q in itertools.pairwise(coords):
            cum.append(cum[-1] + _km(p, q))
        total = cum[-1] or 1.0
        found = []
        for s in self.places:
            if s.kind != "stop" or s.type not in types:
                continue
            dists = [_km(s.point, c) for c in coords]
            i = min(range(len(dists)), key=dists.__getitem__)
            if dists[i] <= max_detour_km:
                found.append((cum[i] / total, s.id, s, dists[i]))
        found.sort(key=lambda f: (f[0], f[1]))
        return [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type,
                "lon": s.lon,
                "lat": s.lat,
                "overnight_ok": s.overnight_ok,
                "est_cost_usd": s.est_cost_usd,
                "distance_from_route_km": round(d, 2),
                "detour_km_estimate": round(2 * d, 1),
                "position_along_route": pos,
            }
            for pos, _, s, d in found[:limit]
        ]

    def hotels_near(
        self, near: Any, radius_km: float, max_price_usd: Any, limit: int
    ) -> list[dict[str, Any]]:
        points = near["coordinates"] if isinstance(near, dict) else [near]
        cap = None if max_price_usd is None else Decimal(str(max_price_usd))
        found = []
        for h in self.places:
            if h.kind != "hotel" or h.price_usd is None:
                continue
            d = min(_km(h.point, p) for p in points)
            if d <= radius_km and (cap is None or h.price_usd <= cap):
                found.append((d, h.price_usd, h.id, h))
        found.sort(key=lambda f: (f[0], f[1], f[2]))
        return [
            {
                "id": h.id,
                "name": h.name,
                "lon": h.lon,
                "lat": h.lat,
                "price_usd": h.price_usd,
                "points_per_night": h.points_per_night,
                "distance_km": round(d, 2),
            }
            for d, _, _, h in found[:limit]
        ]


PLACE = {
    "description": "A corridor town name (e.g. 'Luray, VA') or a [lon, lat] point on the route.",
    "anyOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
    ],
}
PLAN = {
    "type": "object",
    "description": "The full draft plan (spec §5.2). drive_minutes, distance_km and totals "
    "may be 0: they are filled in from the tools.",
}


def tool_specs() -> list[dict[str, Any]]:
    def spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> Any:
        return {
            "toolSpec": {
                "name": name,
                "description": description,
                "inputSchema": {
                    "json": {"type": "object", "properties": properties, "required": required}
                },
            }
        }

    return [
        spec(
            "get_route",
            "Drive between two corridor places, in either direction. Returns distance_km, "
            "duration_min and town-to-town legs.",
            {
                "origin": PLACE,
                "destination": PLACE,
                "waypoints": {"type": "array", "items": PLACE},
            },
            ["origin", "destination"],
        ),
        spec(
            "find_stops_along_route",
            "Wineries, farms, breweries and museums within max_detour_km of the route between "
            "two places, in route order. overnight_ok=true stops can host an RV overnight.",
            {
                "origin": PLACE,
                "destination": PLACE,
                "types": {"type": "array", "items": {"enum": list(STOP_TYPES)}},
                "max_detour_km": {"type": "number", "default": 30},
                "limit": {"type": "integer", "default": 20},
            },
            ["origin", "destination", "types"],
        ),
        spec(
            "find_hotels_near",
            "Hotels within radius_km of a corridor town or point, nearest first, with the cash "
            "price and points per night.",
            {
                "near": PLACE,
                "radius_km": {"type": "number", "default": 15},
                "max_price_usd": {"type": "number"},
                "limit": {"type": "integer", "default": 10},
            },
            ["near"],
        ),
        spec(
            "calculate_trip_cost",
            "Lodging cash total, points for hotel nights, and a per-night breakdown of a draft.",
            {"plan": PLAN},
            ["plan"],
        ),
        spec(
            "validate_plan",
            "Fill in drive times, distances and totals from the tools, then check every rule. "
            "If valid, the plan is returned to the user and you are done; otherwise fix the "
            "listed violations and call it again.",
            {"plan": PLAN},
            ["plan"],
        ),
    ]


def _jsonable(value: Any) -> Any:
    def default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        raise TypeError(f"not JSON serializable: {type(o).__name__}")

    return json.loads(json.dumps(value, default=default))


def _place(value: Any) -> Any:
    if isinstance(value, list):
        return [float(v) for v in value]
    if not isinstance(value, str):
        raise ToolInputError("bad_place", "a place is a corridor town name or [lon, lat]")
    return value


def _point(value: Any) -> list[float]:
    """A town name or [lon, lat] as a point."""
    if isinstance(value, str):
        town = canonical_town(value)
        if town is None:
            raise ToolInputError("outside_coverage", f"{value!r} is not a corridor town")
        return CORRIDOR_TOWNS[town]
    return list(_place(value))


class ToolBox:
    """Runs tool calls for one planner run and remembers what they returned."""

    def __init__(self, catalog: Catalog, spatial: Spatial, request: TripRequest) -> None:
        self.catalog = catalog
        self.spatial = spatial
        self.request = request
        self.allowed_ids: set[str] = set()
        self.accepted: dict[str, Any] | None = None  # a plan that passed validate_plan
        self.last_violations: list[dict[str, Any]] = []
        self.validations = 0
        self.challenged = False  # an acknowledged broken limit was sent back once
        self.challenged_plan: dict[str, Any] | None = None
        self.acknowledged: list[dict[str, Any]] = []

    def call(self, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """(result, ok). Errors are results for the model to act on, never exceptions."""
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": "unknown_tool", "message": f"no tool named {name!r}"}, False
        try:
            return _jsonable(handler(args)), True
        except (RouteError, ToolInputError, CatalogError) as err:
            return _jsonable(err.to_dict()), False
        except (KeyError, TypeError, ValueError, IndexError) as err:
            return {"error": "bad_input", "message": f"{type(err).__name__}: {err}"}, False

    def _tool_get_route(self, args: dict[str, Any]) -> dict[str, Any]:
        route = get_route(
            _place(args["origin"]),
            _place(args["destination"]),
            [_place(w) for w in args.get("waypoints") or []],
        )
        route.pop("geometry")
        return route

    def _tool_find_stops_along_route(self, args: dict[str, Any]) -> dict[str, Any]:
        route = get_route(_place(args["origin"]), _place(args["destination"]))
        stops = self.spatial.stops_along(
            route["geometry"],
            list(args["types"]),
            float(args.get("max_detour_km", 30)),
            int(args.get("limit", 20)),
        )
        self.allowed_ids.update(s["id"] for s in stops)
        return {"stops": stops}

    def _tool_find_hotels_near(self, args: dict[str, Any]) -> dict[str, Any]:
        hotels = self.spatial.hotels_near(
            _point(args["near"]),
            float(args.get("radius_km", 15)),
            args.get("max_price_usd"),
            int(args.get("limit", 10)),
        )
        self.allowed_ids.update(h["id"] for h in hotels)
        return {"hotels": hotels}

    def _draft(self, args: dict[str, Any]) -> dict[str, Any]:
        """The plan from the model, completed, with what the request fixes applied."""
        plan = complete_draft(copy.deepcopy(args["plan"]))
        if not isinstance(plan, dict):
            raise ToolInputError("bad_plan", "plan must be a JSON object")
        plan.setdefault("trip_id", "draft")
        if self.request.vehicle:
            plan["vehicle"] = self.request.vehicle
        previous, from_day = self.request.previous_plan, self.request.replan_from_day
        if previous and from_day and isinstance(plan.get("days"), list):
            kept = copy.deepcopy(previous["days"][: from_day - 1])
            plan["days"] = kept + plan["days"][len(kept) :]
        return fill_numbers(plan, self.catalog)

    def _tool_calculate_trip_cost(self, args: dict[str, Any]) -> dict[str, Any]:
        plan = self._draft(args)
        errors = schema_violations(plan)
        if errors:
            return {"error": "schema", "violations": [v.to_dict() for v in errors]}
        return calculate_trip_cost(plan, self.catalog).to_dict()

    def _tool_validate_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        self.validations += 1
        plan = self._draft(args)
        violations = validate_plan(
            plan,
            self.catalog,
            max_drive_minutes=self.request.max_drive_minutes,
            budget_usd=self.request.budget(),
            previous_plan=self.request.previous_plan,
            replan_from_day=self.request.replan_from_day,
            allowed_ids=self.allowed_ids,
        )
        self.last_violations = [v.to_dict() for v in violations]
        raw_days = plan.get("days")
        days: list[Any] = raw_days if isinstance(raw_days, list) else []
        computed = {
            "days": [
                {k: d.get(k) for k in ("day", "drive_minutes", "distance_km")}
                for d in days
                if isinstance(d, dict)
            ],
            "totals": plan.get("totals"),
            "daily_limit_minutes": self.request.max_drive_minutes,
        }
        hint = self._drive_hint(plan, violations)
        if not blocking(violations):
            if violations and not self.challenged:
                # A warning only excuses a limit that truly can't be met: ask once for a fix.
                self.challenged = True
                self.challenged_plan = plan
                return {
                    "valid": False,
                    "needs_confirmation": True,
                    "violations": self.last_violations,
                    "computed": computed,
                    "message": "This plan breaks a hard limit and only passes because a "
                    "warning names it. Compare each day's drive_minutes with "
                    "daily_limit_minutes: if moving overnights (or picking cheaper places) can "
                    "keep every day within the limits, fix it and call validate_plan again. If "
                    "it is truly impossible with the days and budget requested, call "
                    "validate_plan again with this same plan. Do not reply in text.",
                    **({"hint": hint} if hint else {}),
                }
            self.accept(plan)
            return {"valid": True, "acknowledged": self.last_violations}
        return {
            "valid": False,
            "violations": [v.to_dict() for v in blocking(violations)],
            "computed": computed,
        }

    def _drive_hint(self, plan: dict[str, Any], violations: list[Any]) -> str | None:
        """A hint for the challenge when a max_drive violation looks fixable: the route left to
        plan (from the replanned day on) fits the daily limit on average. Only a hint: where
        places exist (e.g. RV hosts) can still make a long day unavoidable. None otherwise."""
        if not any(v.code == "max_drive" for v in violations):
            return None
        days = plan["days"]
        first = (self.request.replan_from_day or 1) - 1  # index of the first changeable day
        start: Any = plan["origin"]
        if first > 0:
            ids = [d["overnight"]["id"] for d in days if d.get("overnight")]
            ends = day_ends(plan, self.catalog.get(ids))
            if ends is None:
                return None
            start = ends[first - 1]
        try:
            total = float(get_route(start, plan["destination"])["duration_min"])
        except RouteError:
            return None
        left = len(days) - first
        limit = self.request.max_drive_minutes
        if left <= 0 or total / left > limit:
            return None
        return (
            f"The route left to plan is {total:.0f} min over {left} days, about "
            f"{total / left:.0f} min a day, within the {limit:.0f} min daily limit. Move the "
            "overnights so every day's drive_minutes is under the limit; check each town's "
            "position with get_route."
        )

    def accept(self, plan: dict[str, Any]) -> None:
        self.accepted = plan
        self.acknowledged = [v for v in self.last_violations if v.get("acknowledged")]

    def confirm_challenged(self) -> bool:
        """A text reply after a challenge counts as confirming the challenged plan."""
        if self.challenged_plan is None or self.accepted is not None:
            return False
        plan = self.challenged_plan
        self.last_violations = [
            v.to_dict()
            for v in validate_plan(
                plan,
                self.catalog,
                max_drive_minutes=self.request.max_drive_minutes,
                budget_usd=self.request.budget(),
                previous_plan=self.request.previous_plan,
                replan_from_day=self.request.replan_from_day,
                allowed_ids=self.allowed_ids,
            )
        ]
        self.accept(plan)
        return True


def timed_call(box: ToolBox, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool, int]:
    started = time.perf_counter()
    result, ok = box.call(name, args)
    return result, ok, round((time.perf_counter() - started) * 1000)
