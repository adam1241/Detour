"""Per-plan checks and suite scoring (spec §8.1, ADR-004).

Each check is True (pass), False (fail) or None (not applicable to this case). A broken hard
constraint (max drive, budget) passes only when the case is labeled infeasible AND the plan
acknowledges that exact violation with a warning and an alternative.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent.plan.catalog import Catalog
from agent.plan.validate import Violation, validate_plan
from agent.tools.get_route import canonical_town
from evals.cases import EvalCase

if TYPE_CHECKING:
    from evals.runner import PlannerOutput

# Gated checks and their merge thresholds (spec §8.1).
GATED: dict[str, float] = {
    "schema": 1.0,
    "grounding": 1.0,
    "max_drive": 0.95,
    "budget": 0.95,
    "route_order": 0.95,
    "vehicle": 0.95,
    "replan_earlier_days": 0.95,
}
# Day/overnight structure: a plan with these can't be checked for drive time or route order.
STRUCTURE_CODES = {"day_numbers", "overnight_missing", "last_day_overnight"}
# IDs that don't exist or weren't returned by a tool, and numbers that don't match the tools.
GROUNDING_CODES = {
    "unknown_id",
    "not_from_tools",
    "kind_mismatch",
    "type_mismatch",
    "drive_mismatch",
    "distance_mismatch",
    "totals_mismatch",
}


@dataclass
class CaseResult:
    id: str
    checks: dict[str, bool | None]
    violations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    plan: Any = None
    latency_s: float | None = None
    first_progress_s: float | None = None
    cost_usd: float | None = None
    outdoor_leaning: bool = False
    hotel_nights: int = 0


def _constraint(violations: list[Violation], code: str, case: EvalCase) -> bool:
    found = [v for v in violations if v.code == code]
    return not found or (case.infeasible and all(v.acknowledged for v in found))


def _request_mismatches(case: EvalCase, plan: dict[str, Any]) -> list[str]:
    """Where the plan doesn't answer the trip that was asked for (when the case specifies it)."""
    problems = []
    if canonical_town(plan["origin"]) != canonical_town(case.origin):
        problems.append(f"origin is {plan['origin']!r}, the request starts at {case.origin!r}")
    if case.destination is not None and canonical_town(plan["destination"]) != canonical_town(
        case.destination
    ):
        problems.append(
            f"destination is {plan['destination']!r}, the request ends at {case.destination!r}"
        )
    if case.days is not None and len(plan["days"]) != case.days:
        problems.append(f"{len(plan['days'])} days, the request asks for {case.days}")
    return problems


def check_case(
    case: EvalCase,
    output: PlannerOutput,
    catalog: Catalog,
    previous_plan: dict[str, Any] | None = None,
) -> CaseResult:
    applicable = {
        "schema": True,
        "grounding": True,
        "max_drive": True,
        "budget": case.budget_usd is not None,
        "route_order": True,
        "vehicle": True,
        "replan_earlier_days": case.replan is not None,
    }

    def all_fail() -> dict[str, bool | None]:
        return {k: (False if on else None) for k, on in applicable.items()}

    result = CaseResult(
        id=case.id,
        checks=all_fail(),
        error=output.error,
        plan=output.plan,
        latency_s=output.latency_s,
        first_progress_s=output.first_progress_s,
        cost_usd=output.cost_usd,
        outdoor_leaning=case.outdoor_leaning,
    )
    if output.plan is None:
        return result

    violations = validate_plan(
        output.plan,
        catalog,
        max_drive_minutes=case.max_drive_minutes,
        budget_usd=case.budget_usd,
        previous_plan=previous_plan,
        replan_from_day=case.replan.from_day if case.replan else None,
        allowed_ids=output.allowed_ids,
    )
    result.violations = [v.to_dict() for v in violations]
    codes = {v.code for v in violations}
    if "schema" in codes:
        return result
    # The "schema" gate also covers the plan's structure and whether it answers the request:
    # a plan for the wrong trip, or with missing overnights, can't pass the dependent checks.
    mismatches = _request_mismatches(case, output.plan)
    result.violations += [
        {"code": "request_mismatch", "message": m, "day": None, "acknowledged": False}
        for m in mismatches
    ]
    if mismatches or codes & STRUCTURE_CODES:
        return result

    result.hotel_nights = int(output.plan["totals"]["hotel_nights"])
    checks: dict[str, bool | None] = {
        "schema": True,
        "grounding": not (codes & GROUNDING_CODES),
        "max_drive": _constraint(violations, "max_drive", case),
        "budget": _constraint(violations, "budget", case),
        "route_order": not (codes & {"backtracking", "route_error"}),
        "vehicle": "vehicle_rule" not in codes and output.plan["vehicle"] == case.vehicle,
        "replan_earlier_days": (
            "replan_changed_earlier_day" not in codes and previous_plan is not None
        ),
    }
    result.checks = {k: (v if applicable[k] else None) for k, v in checks.items()}
    return result


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def score(
    rows: list[dict[str, bool | None]],
    baseline: dict[str, Any] | None = None,
    tracked: list[CaseResult] | None = None,
) -> dict[str, Any]:
    """Pass rates per gated check, the merge gate, and tracked (ungated) metrics.

    Gate: every check at or above its threshold, and no check more than one case worse than
    `main`'s baseline (zero tolerance for 100% checks).
    """
    checks: dict[str, dict[str, Any]] = {}
    regressions: list[str] = []
    for name, threshold in GATED.items():
        values = [r.get(name) for r in rows if r.get(name) is not None]
        passed = sum(1 for v in values if v)
        failures = len(values) - passed
        rate = passed / len(values) if values else 1.0
        checks[name] = {
            "passed": passed,
            "applicable": len(values),
            "failures": failures,
            "rate": rate,
            "threshold": threshold,
            "ok": rate >= threshold,
        }
        if baseline is not None and name in baseline.get("checks", {}):
            base_failures = baseline["checks"][name]["failures"]
            allowed = base_failures + (0 if threshold >= 1.0 else 1)
            if failures > allowed:
                regressions.append(f"{name}: {failures} failures vs {base_failures} on main")

    summary: dict[str, Any] = {
        "checks": checks,
        "regressions": regressions,
        "gate_ok": all(c["ok"] for c in checks.values()) and not regressions,
        "tracked": {},
    }
    if tracked is not None:
        outdoor = [r for r in tracked if r.outdoor_leaning]
        latencies = [r.latency_s for r in tracked if r.latency_s is not None]
        first_progress = [r.first_progress_s for r in tracked if r.first_progress_s is not None]
        costs = [r.cost_usd for r in tracked if r.cost_usd is not None]
        summary["tracked"] = {
            "hotel_conversion": (
                sum(1 for r in outdoor if r.hotel_nights >= 1) / len(outdoor) if outdoor else None
            ),
            "first_progress_p50_s": _percentile(first_progress, 50),
            "first_progress_p95_s": _percentile(first_progress, 95),
            "latency_p50_s": _percentile(latencies, 50),
            "latency_p95_s": _percentile(latencies, 95),
            "mean_cost_usd": statistics.fmean(costs) if costs else None,
        }
    return summary
