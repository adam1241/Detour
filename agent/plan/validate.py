"""validate_plan tool (spec §5.1, §5.2, §5.3).

Returns every rule violation found (empty list = valid). Distances and drive times are
recomputed with get_route plus estimated side trips, so the agent can't invent them. A broken
hard constraint (max drive per day, budget) counts as acknowledged, not blocking, only when a
warning names that specific violation and the plan offers at least one alternative.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.plan.catalog import Catalog, Place
from agent.plan.cost import calculate_trip_cost
from agent.tools.get_route import Place as RoutePlace
from agent.tools.get_route import RouteError, distance_from_route_km, get_route

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "packages" / "schema" / "plan.schema.json"
CONSTRAINT_CODES = {"max_drive", "budget"}
DRIVE_TOLERANCE_MIN, DRIVE_TOLERANCE_PCT = 5.0, 0.05
DISTANCE_TOLERANCE_KM, DISTANCE_TOLERANCE_PCT = 2.0, 0.05
ORDER_TOLERANCE_KM = 1.0
SEGMENT_TOLERANCE_KM, SEGMENT_TOLERANCE_PCT = 5.0, 0.02
# Side trips off the corridor road: straight-line km x 1.3 (road factor) at 50 km/h.
SIDE_TRIP_ROAD_FACTOR, SIDE_TRIP_KMH = 1.3, 50.0
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=\S)")


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    day: int | None = None
    acknowledged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "day": self.day,
            "acknowledged": self.acknowledged,
        }


def blocking(violations: list[Violation]) -> list[Violation]:
    """Violations the agent must still fix."""
    return [v for v in violations if not v.acknowledged]


@cache
def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


def _schema_violations(plan: object) -> list[Violation]:
    if not isinstance(plan, dict):
        return [Violation("schema", "plan must be a JSON object")]
    errors = sorted(_schema_validator().iter_errors(plan), key=lambda e: list(map(str, e.path)))
    out = [
        Violation("schema", f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}")
        for e in errors[:10]
    ]
    if out:
        return out
    # JSON can't carry NaN/inf, but a Python draft can; they'd slip past every comparison.
    numbers = [("totals/cash_usd", plan["totals"]["cash_usd"])] + [
        (f"days/{i}/{field}", d[field])
        for i, d in enumerate(plan["days"])
        for field in ("drive_minutes", "distance_km")
    ]
    return [
        Violation("schema", f"{path}: must be a finite number")
        for path, value in numbers
        if isinstance(value, float) and not math.isfinite(value)
    ]


def _sentences(text: str) -> int:
    return len([s for s in SENTENCE_SPLIT.split(text.strip()) if s])


def _route(start: RoutePlace, end: RoutePlace) -> tuple[float, float]:
    """(minutes, km) from get_route; 0 when both ends are the same place."""
    try:
        r = get_route(start, end)
    except RouteError as err:
        if err.code == "same_place":
            return 0.0, 0.0
        raise
    return float(r["duration_min"]), float(r["distance_km"])


def _off(actual: float, expected: float, absolute: float, pct: float) -> bool:
    return abs(actual - expected) > max(absolute, pct * expected)


def _side_trip_km(point: Sequence[float]) -> float:
    return distance_from_route_km(point) * SIDE_TRIP_ROAD_FACTOR


def _grounding(
    plan: dict[str, Any], places: dict[str, Place], allowed_ids: Collection[str] | None
) -> list[Violation]:
    out: list[Violation] = []
    for d in plan["days"]:
        n = d["day"]
        ids = [s["id"] for s in d["stops"]] + ([d["overnight"]["id"]] if d["overnight"] else [])
        if allowed_ids is not None:
            out += [
                Violation("not_from_tools", f"{i} wasn't returned by a tool in this run", n)
                for i in ids
                if i not in allowed_ids
            ]
        for s in d["stops"]:
            place = places.get(s["id"])
            if place is None:
                out.append(Violation("unknown_id", f"{s['id']} is not a known stop", n))
            elif place.kind != "stop":
                out.append(Violation("kind_mismatch", f"{s['id']} is not a stop", n))
            elif place.type != s["type"]:
                out.append(
                    Violation("type_mismatch", f"{s['id']} is a {place.type}, not {s['type']}", n)
                )
        o = d["overnight"]
        if o is None:
            continue
        place = places.get(o["id"])
        if place is None:
            out.append(Violation("unknown_id", f"{o['id']} is not a known place", n))
            continue
        if place.kind != ("hotel" if o["kind"] == "hotel" else "stop"):
            out.append(Violation("kind_mismatch", f"{o['id']} can't be a {o['kind']} night", n))
            continue
        if o["kind"] == "host":
            if not place.overnight_ok:
                out.append(Violation("not_overnight_host", f"{o['id']} doesn't host overnights", n))
            if plan["vehicle"] == "car":
                out.append(
                    Violation(
                        "vehicle_rule", "car trips sleep in hotels; host overnights need rv", n
                    )
                )
        if _sentences(o["reason"]) != 1:
            out.append(Violation("reason_format", "an overnight reason is exactly one sentence", n))
    return out


def _known_point(places: dict[str, Place], pid: str, kind: str) -> list[float] | None:
    place = places.get(pid)
    return place.point if place is not None and place.kind == kind else None


def _routing(
    plan: dict[str, Any], places: dict[str, Place], max_drive_minutes: float
) -> list[Violation]:
    """Drive time, distance and route order. Needs every overnight resolved; unknown stops
    are skipped (they're already reported) instead of hiding the other checks."""
    out: list[Violation] = []
    origin, destination = plan["origin"], plan["destination"]
    days = plan["days"]
    ends: list[list[float] | str] = []
    for d in days:
        if d["overnight"] is None:
            ends.append(destination)
            continue
        kind = "hotel" if d["overnight"]["kind"] == "hotel" else "stop"
        point = _known_point(places, d["overnight"]["id"], kind)
        if point is None:
            return out
        ends.append(point)

    start: RoutePlace = origin
    for d, end in zip(days, ends, strict=True):
        n = d["day"]
        try:
            corridor_min, corridor_km = _route(start, end)
        except RouteError as err:
            return [*out, Violation("route_error", err.message, n)]
        stop_points = [p for s in d["stops"] if (p := _known_point(places, s["id"], "stop"))]
        side_km = sum(2 * _side_trip_km(p) for p in stop_points)
        side_km += sum(_side_trip_km(list(p)) for p in (start, end) if not isinstance(p, str))
        side_min = side_km * 60 / SIDE_TRIP_KMH
        minutes, km = corridor_min + side_min, corridor_km + side_km
        basis = f"get_route says {corridor_min:.0f} min + {side_min:.0f} min of side trips"
        if _off(d["drive_minutes"], minutes, DRIVE_TOLERANCE_MIN, DRIVE_TOLERANCE_PCT):
            out.append(
                Violation("drive_mismatch", f"drive_minutes {d['drive_minutes']} but {basis}", n)
            )
        if _off(d["distance_km"], km, DISTANCE_TOLERANCE_KM, DISTANCE_TOLERANCE_PCT):
            out.append(
                Violation(
                    "distance_mismatch",
                    f"distance_km {d['distance_km']} but get_route says {corridor_km:.1f} km "
                    f"+ {side_km:.1f} km of side trips",
                    n,
                )
            )
        if minutes > max_drive_minutes:
            out.append(
                Violation(
                    "max_drive",
                    f"Day {n}: {minutes:.0f} min of driving, over the {max_drive_minutes:.0f} "
                    "min limit",
                    n,
                )
            )
        start = end

    # Route order: every place must lie on the way from origin to destination, and its
    # distance along that way must never go down.
    try:
        _, total_km = _route(origin, destination)
    except RouteError as err:
        return [*out, Violation("route_error", err.message)]
    slack = max(SEGMENT_TOLERANCE_KM, SEGMENT_TOLERANCE_PCT * total_km)
    last_km, last_label = 0.0, str(origin)
    for d, end in zip(days, ends, strict=True):
        sequence = [(s["id"], _known_point(places, s["id"], "stop")) for s in d["stops"]]
        if d["overnight"] is not None:
            sequence.append((d["overnight"]["id"], end if not isinstance(end, str) else None))
        for label, point in sequence:
            if point is None:
                continue
            try:
                from_origin = _route(origin, point)[1]
                to_destination = _route(point, destination)[1]
            except RouteError as err:
                return [*out, Violation("route_error", err.message, d["day"])]
            if from_origin + to_destination > total_km + slack:
                out.append(
                    Violation(
                        "backtracking",
                        f"{label} is not on the way from {origin} to {destination}",
                        d["day"],
                    )
                )
            elif from_origin + ORDER_TOLERANCE_KM < last_km:
                out.append(
                    Violation(
                        "backtracking",
                        f"{label} is {last_km - from_origin:.0f} km back toward {origin} "
                        f"from {last_label}",
                        d["day"],
                    )
                )
            else:
                last_km, last_label = max(last_km, from_origin), label
    return out


def _money(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _acknowledged(v: Violation, warnings: list[str], alternatives: list[str]) -> bool:
    """A broken constraint is acknowledged only by a warning about that exact violation."""
    if not alternatives:
        return False
    if v.code == "max_drive":
        day = re.compile(rf"\bday\s*{v.day}\b", re.IGNORECASE)
        return any(day.search(w) and re.search(r"driv", w, re.IGNORECASE) for w in warnings)
    if v.code == "budget":
        return any(re.search(r"budget", w, re.IGNORECASE) for w in warnings)
    return False


def validate_plan(
    plan: Any,
    catalog: Catalog,
    *,
    max_drive_minutes: float = 300,
    budget_usd: Decimal | None = None,
    previous_plan: dict[str, Any] | None = None,
    replan_from_day: int | None = None,
    allowed_ids: Collection[str] | None = None,
) -> list[Violation]:
    """Every rule violation in a draft plan. Empty list = valid.

    `allowed_ids`: the place IDs tools returned during this run. When given, any other ID is a
    `not_from_tools` violation even if it exists in the catalog (spec §5.1).
    """
    schema_errors = _schema_violations(plan)
    if schema_errors:
        return schema_errors

    days = plan["days"]
    out: list[Violation] = []
    if [d["day"] for d in days] != list(range(1, len(days) + 1)):
        out.append(Violation("day_numbers", "days must be numbered 1, 2, 3, ... in order"))
    for i, d in enumerate(days):
        last = i == len(days) - 1
        if last and d["overnight"] is not None:
            out.append(
                Violation("last_day_overnight", "the last day ends at the destination", d["day"])
            )
        if not last and d["overnight"] is None:
            out.append(
                Violation(
                    "overnight_missing", "every day but the last needs an overnight", d["day"]
                )
            )
        if not 2 <= _sentences(d["narrative"]) <= 3:
            out.append(Violation("narrative_length", "a day narrative is 2-3 sentences", d["day"]))

    ids = [s["id"] for d in days for s in d["stops"]] + [
        d["overnight"]["id"] for d in days if d["overnight"]
    ]
    places = catalog.get(ids)  # one snapshot for grounding, routing and cost
    out += _grounding(plan, places, allowed_ids)

    if not any(v.code in ("overnight_missing", "last_day_overnight") for v in out):
        out += _routing(plan, places, max_drive_minutes)

    cost = calculate_trip_cost(plan, catalog, places)
    if not cost.unknown_ids and not cost.mismatched_ids:
        if budget_usd is not None and cost.cash_usd > budget_usd:
            out.append(
                Violation("budget", f"lodging is ${cost.cash_usd}, over the ${budget_usd} budget")
            )
        totals = plan["totals"]
        if (
            _money(totals["cash_usd"]) != cost.cash_usd
            or totals["points"] != cost.points
            or totals["hotel_nights"] != cost.hotel_nights
            or totals["host_nights"] != cost.host_nights
        ):
            out.append(
                Violation(
                    "totals_mismatch",
                    f"totals must equal calculate_trip_cost: ${cost.cash_usd}, {cost.points} pts, "
                    f"{cost.hotel_nights} hotel / {cost.host_nights} host nights",
                )
            )

    if previous_plan is not None and replan_from_day is not None:
        before = previous_plan.get("days", [])[: replan_from_day - 1]
        for k, old_day in enumerate(before):
            if k >= len(days) or days[k] != old_day:
                out.append(
                    Violation(
                        "replan_changed_earlier_day",
                        "days before the replan must not change",
                        k + 1,
                    )
                )

    warnings, alternatives = plan["warnings"], plan["alternatives"]
    return [
        replace(v, acknowledged=_acknowledged(v, warnings, alternatives))
        if v.code in CONSTRAINT_CODES
        else v
        for v in out
    ]
