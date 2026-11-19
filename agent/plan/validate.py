"""validate_plan tool (spec §5.1, §5.2, §5.3).

Returns every rule violation found (empty list = valid). Distances and drive times are
recomputed with get_route, so the agent can't invent them. Hard constraints (max drive per
day, budget) count as acknowledged, not blocking, only when the plan carries at least one
warning and one alternative (spec §5.1, §8.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.plan.catalog import Catalog, Place
from agent.plan.cost import calculate_trip_cost
from agent.tools.get_route import Place as RoutePlace
from agent.tools.get_route import RouteError, get_route

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "packages" / "schema" / "plan.schema.json"
CONSTRAINT_CODES = {"max_drive", "budget"}
DRIVE_TOLERANCE_MIN, DRIVE_TOLERANCE_PCT = 5.0, 0.05
DISTANCE_TOLERANCE_KM, DISTANCE_TOLERANCE_PCT = 2.0, 0.05
ORDER_TOLERANCE_KM = 1.0


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
    return [
        Violation("schema", f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}")
        for e in errors[:10]
    ]


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


def _grounding(plan: dict[str, Any], places: dict[str, Place]) -> tuple[list[Violation], bool]:
    out: list[Violation] = []
    grounded = True
    for d in plan["days"]:
        n = d["day"]
        for s in d["stops"]:
            place = places.get(s["id"])
            if place is None:
                out.append(Violation("unknown_id", f"{s['id']} is not a known stop", n))
                grounded = False
            elif place.kind != "stop":
                out.append(Violation("kind_mismatch", f"{s['id']} is not a stop", n))
                grounded = False
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
            grounded = False
            continue
        expected_kind = "hotel" if o["kind"] == "hotel" else "stop"
        if place.kind != expected_kind:
            out.append(Violation("kind_mismatch", f"{o['id']} can't be a {o['kind']} night", n))
            grounded = False
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
    return out, grounded


def _routing(
    plan: dict[str, Any], places: dict[str, Place], max_drive_minutes: float
) -> list[Violation]:
    out: list[Violation] = []
    origin, destination = plan["origin"], plan["destination"]
    start: RoutePlace = origin
    for d in plan["days"]:
        n = d["day"]
        end: RoutePlace = places[d["overnight"]["id"]].point if d["overnight"] else destination
        try:
            minutes, km = _route(start, end)
        except RouteError as err:
            return [*out, Violation("route_error", err.message, n)]
        if _off(d["drive_minutes"], minutes, DRIVE_TOLERANCE_MIN, DRIVE_TOLERANCE_PCT):
            out.append(
                Violation(
                    "drive_mismatch",
                    f"drive_minutes {d['drive_minutes']} but get_route says {minutes:.0f}",
                    n,
                )
            )
        if _off(d["distance_km"], km, DISTANCE_TOLERANCE_KM, DISTANCE_TOLERANCE_PCT):
            out.append(
                Violation(
                    "distance_mismatch",
                    f"distance_km {d['distance_km']} but get_route says {km:.1f}",
                    n,
                )
            )
        if minutes > max_drive_minutes:
            out.append(
                Violation(
                    "max_drive",
                    f"{minutes:.0f} min of driving, over the {max_drive_minutes:.0f} min limit",
                    n,
                )
            )
        start = end

    # Route order: distance along the route from the origin must never go down.
    last_km, last_label = 0.0, str(origin)
    for d in plan["days"]:
        sequence = [(s["id"], places[s["id"]].point) for s in d["stops"]]
        if d["overnight"]:
            sequence.append((d["overnight"]["id"], places[d["overnight"]["id"]].point))
        for label, point in sequence:
            try:
                km_from_origin = _route(origin, point)[1]
            except RouteError as err:
                return [*out, Violation("route_error", err.message, d["day"])]
            if km_from_origin + ORDER_TOLERANCE_KM < last_km:
                out.append(
                    Violation(
                        "backtracking",
                        f"{label} is {last_km - km_from_origin:.0f} km back toward {origin} "
                        f"from {last_label}",
                        d["day"],
                    )
                )
            else:
                last_km, last_label = max(last_km, km_from_origin), label
    return out


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def validate_plan(
    plan: Any,
    catalog: Catalog,
    *,
    max_drive_minutes: float = 300,
    budget_usd: Decimal | None = None,
    previous_plan: dict[str, Any] | None = None,
    replan_from_day: int | None = None,
) -> list[Violation]:
    """Every rule violation in a draft plan. Empty list = valid."""
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

    ids = [s["id"] for d in days for s in d["stops"]] + [
        d["overnight"]["id"] for d in days if d["overnight"]
    ]
    places = catalog.get(ids)
    grounding, grounded = _grounding(plan, places)
    out += grounding

    if grounded and not any(v.code in ("overnight_missing", "last_day_overnight") for v in out):
        out += _routing(plan, places, max_drive_minutes)

    if grounded:
        cost = calculate_trip_cost(plan, catalog)
        if budget_usd is not None and cost.cash_usd > budget_usd:
            out.append(
                Violation("budget", f"lodging is ${cost.cash_usd}, over the ${budget_usd} budget")
            )
        totals = plan["totals"]
        stated_cash = _money(totals["cash_usd"])
        if (
            stated_cash is None
            or abs(stated_cash - cost.cash_usd) > Decimal("0.01")
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

    acknowledged = bool(plan["warnings"]) and bool(plan["alternatives"])
    return [replace(v, acknowledged=acknowledged) if v.code in CONSTRAINT_CODES else v for v in out]
