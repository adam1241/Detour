"""Eval suite: case files, per-plan checks, scoring and the runner (spec §8.1, ADR-004).

Uses the real seed data through an in-memory catalog, so no database is needed.
"""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agent.tools.get_route import get_route
from evals.cases import EvalCase, Replan, load_cases
from evals.catalog import seed_catalog
from evals.checks import GATED, check_case, score
from evals.runner import PlannerOutput, run, write_results

CASES = {c.id: c for c in load_cases()}
CATALOG = seed_catalog()


def luray_weekend_plan() -> dict[str, Any]:
    """A valid plan for c04: DC to Luray, 2 days, one night at Quality Inn Luray."""
    luray_hotel = CATALOG.get(["hotel_quality_inn_luray"])["hotel_quality_inn_luray"].point
    first = get_route("Washington, DC", luray_hotel)
    return {
        "trip_id": "t_c04",
        "summary": "A short family weekend in Luray.",
        "origin": "Washington, DC",
        "destination": "Luray, VA",
        "vehicle": "car",
        "days": [
            {
                "day": 1,
                "drive_minutes": first["duration_min"],
                "distance_km": first["distance_km"],
                "stops": [],
                "overnight": {
                    "kind": "hotel",
                    "id": "hotel_quality_inn_luray",
                    "reason_code": "drive_time",
                    "reason": "About two hours of driving keeps the kids happy.",
                },
                "narrative": "Head west into the Blue Ridge. Check in early in Luray.",
            },
            {
                "day": 2,
                "drive_minutes": 0,
                "distance_km": 0,
                "stops": [
                    {
                        "id": "stop_car_carriage_caravan_museum",
                        "type": "museum",
                        "reason": "Right in town.",
                    }
                ],
                "overnight": None,
                "narrative": "Spend the morning at the caverns. Enjoy a slow day in town.",
            },
        ],
        "totals": {"cash_usd": 73.0, "points": 12000, "hotel_nights": 1, "host_nights": 0},
        "warnings": [],
        "alternatives": [],
    }


def one_day_plan(warn: bool) -> dict[str, Any]:
    """c08: DC to Asheville in one day, which breaks the 5 h limit."""
    r = get_route("Washington, DC", "Asheville, NC")
    return {
        "trip_id": "t_c08",
        "summary": "DC to Asheville in a single day.",
        "origin": "Washington, DC",
        "destination": "Asheville, NC",
        "vehicle": "car",
        "days": [
            {
                "day": 1,
                "drive_minutes": r["duration_min"],
                "distance_km": r["distance_km"],
                "stops": [],
                "overnight": None,
                "narrative": "A very long day on the road. Leave early.",
            }
        ],
        "totals": {"cash_usd": 0, "points": 0, "hotel_nights": 0, "host_nights": 0},
        "warnings": ["Day 1 is about 10.6 hours of driving, over your 5 hour limit."]
        if warn
        else [],
        "alternatives": ["Split it into 3 days with nights in Staunton and Boone."] if warn else [],
    }


# --- case files ------------------------------------------------------------------------------


def test_twenty_cases_with_unique_ids() -> None:
    cases = load_cases()
    assert len(cases) == 20
    assert len({c.id for c in cases}) == 20


def test_cases_cover_personas_vehicles_and_edge_cases() -> None:
    cases = list(CASES.values())
    assert {c.persona for c in cases} == {"rv_couple", "family", "weekend_explorer"}
    assert {c.vehicle for c in cases} == {"rv", "car"}
    assert sum(c.infeasible for c in cases) >= 3
    assert sum(c.replan is not None for c in cases) == 3
    assert any(c.destination is None for c in cases)  # vague request


def test_case_places_are_on_the_corridor() -> None:
    for c in CASES.values():
        if c.destination is not None:
            get_route(c.origin, c.destination)  # raises RouteError if not


def test_replans_point_at_an_earlier_case() -> None:
    order = [c.id for c in load_cases()]
    for c in CASES.values():
        if c.replan:
            assert c.replan.of in CASES
            assert order.index(c.replan.of) < order.index(c.id)


def test_infeasible_labels_match_the_data() -> None:
    dc_asheville = get_route("Washington, DC", "Asheville, NC")["duration_min"]
    assert dc_asheville > CASES["c08_one_day"].max_drive_minutes
    assert dc_asheville / 4 > CASES["c17_two_hour_days"].max_drive_minutes
    cheapest = sorted(p.price_usd or 0 for p in CATALOG.all() if p.kind == "hotel")[:3]
    assert sum(cheapest) > (CASES["c09_impossible_budget"].budget_usd or 0)


def test_bad_case_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("id: x\nrequest: hi\n")
    with pytest.raises(ValueError, match="missing"):
        load_cases(tmp_path)


# --- check_case -------------------------------------------------------------------------------


def test_valid_plan_passes_every_applicable_check() -> None:
    result = check_case(
        CASES["c04_family_short_hop"], PlannerOutput(plan=luray_weekend_plan()), CATALOG
    )
    assert result.checks == {
        "schema": True,
        "grounding": True,
        "max_drive": True,
        "budget": True,
        "route_order": True,
        "vehicle": True,
        "replan_earlier_days": None,
    }
    assert result.violations == []


def test_no_plan_fails_every_applicable_check() -> None:
    result = check_case(
        CASES["c04_family_short_hop"], PlannerOutput(plan=None, error="timeout"), CATALOG
    )
    assert {k for k, v in result.checks.items() if v is False} == {
        "schema",
        "grounding",
        "max_drive",
        "budget",
        "route_order",
        "vehicle",
    }


def test_invented_numbers_fail_grounding() -> None:
    plan = luray_weekend_plan()
    plan["days"][0]["drive_minutes"] = 30
    result = check_case(CASES["c04_family_short_hop"], PlannerOutput(plan=plan), CATALOG)
    assert result.checks["grounding"] is False


def test_broken_limit_passes_only_on_an_infeasible_case_with_warning_and_alternative() -> None:
    one_day = CASES["c08_one_day"]
    assert (
        check_case(one_day, PlannerOutput(plan=one_day_plan(warn=True)), CATALOG).checks[
            "max_drive"
        ]
        is True
    )
    assert (
        check_case(one_day, PlannerOutput(plan=one_day_plan(warn=False)), CATALOG).checks[
            "max_drive"
        ]
        is False
    )
    feasible = replace(one_day, infeasible=False)
    assert (
        check_case(feasible, PlannerOutput(plan=one_day_plan(warn=True)), CATALOG).checks[
            "max_drive"
        ]
        is False
    )


def test_budget_is_not_applicable_without_a_budget() -> None:
    case = replace(CASES["c04_family_short_hop"], budget_usd=None)
    assert (
        check_case(case, PlannerOutput(plan=luray_weekend_plan()), CATALOG).checks["budget"] is None
    )


def test_replan_check_uses_the_previous_plan() -> None:
    base = luray_weekend_plan()
    replan_case = replace(
        CASES["c04_family_short_hop"], replan=CASES["c19_replan_shorter_day2"].replan
    )
    changed = json.loads(json.dumps(base))
    changed["days"][0]["narrative"] = "Something else entirely. Different words."
    result = check_case(replan_case, PlannerOutput(plan=changed), CATALOG, previous_plan=base)
    assert result.checks["replan_earlier_days"] is False
    same = check_case(replan_case, PlannerOutput(plan=base), CATALOG, previous_plan=base)
    assert same.checks["replan_earlier_days"] is True


# --- score ------------------------------------------------------------------------------------


def _results(fails: dict[str, int], total: int = 20) -> list[dict[str, bool | None]]:
    rows = []
    for i in range(total):
        row: dict[str, bool | None] = {name: not (i < fails.get(name, 0)) for name in GATED}
        rows.append(row)
    return rows


def test_thresholds_gate_the_merge() -> None:
    assert score(_results({}))["gate_ok"] is True
    assert score(_results({"max_drive": 1}))["gate_ok"] is True  # 95%
    assert score(_results({"max_drive": 2}))["gate_ok"] is False  # 90%
    assert score(_results({"grounding": 1}))["gate_ok"] is False  # must be 100%


def test_at_most_one_case_worse_than_main() -> None:
    baseline = score(_results({}, total=40))
    assert score(_results({"route_order": 1}, total=40), baseline)["gate_ok"] is True
    regressed = score(_results({"route_order": 2}, total=40), baseline)
    assert regressed["gate_ok"] is False
    assert regressed["regressions"] == ["route_order: 2 failures vs 0 on main"]


def test_not_applicable_checks_are_left_out_of_the_rate() -> None:
    rows = _results({})
    for r in rows:
        r["budget"] = None
    s = score(rows)
    assert s["checks"]["budget"]["applicable"] == 0
    assert s["gate_ok"] is True


# --- runner -----------------------------------------------------------------------------------


def test_runner_end_to_end_with_a_stub_planner(tmp_path: Path) -> None:
    cases = [CASES["c04_family_short_hop"], CASES["c08_one_day"]]

    def stub(case: EvalCase, previous: dict[str, Any] | None) -> PlannerOutput:
        plan = luray_weekend_plan() if case.id.startswith("c04") else one_day_plan(warn=True)
        return PlannerOutput(plan=plan, latency_s=1.5, cost_usd=0.01)

    results = run(stub, CATALOG, cases)
    summary = score([r.checks for r in results], tracked=results)
    assert summary["gate_ok"] is True
    assert summary["tracked"]["latency_p50_s"] == 1.5
    json_path, md_path = write_results(results, summary, tmp_path, sha="abc1234")
    saved = json.loads(json_path.read_text())
    assert saved["sha"] == "abc1234"
    assert [c["id"] for c in saved["cases"]] == ["c04_family_short_hop", "c08_one_day"]
    assert "| grounding | 2/2 | 100% | 100% | ✅ |" in md_path.read_text(encoding="utf-8")


def test_planner_crash_is_recorded_not_raised() -> None:
    def broken(case: EvalCase, previous: dict[str, Any] | None) -> PlannerOutput:
        raise RuntimeError("model timeout")

    [result] = run(broken, CATALOG, [CASES["c04_family_short_hop"]])
    assert result.error == "RuntimeError: model timeout"
    assert result.checks["schema"] is False


def test_hotel_conversion_counts_outdoor_trips_with_a_hotel_night() -> None:
    case = replace(CASES["c04_family_short_hop"], outdoor_leaning=True)
    results = run(lambda c, p: PlannerOutput(plan=luray_weekend_plan()), CATALOG, [case])
    assert score([r.checks for r in results], tracked=results)["tracked"]["hotel_conversion"] == 1.0


def test_budget_is_decimal() -> None:
    assert CASES["c01_rv_couple_classic"].budget_usd == Decimal("600")


# --- review follow-ups -------------------------------------------------------------------------


def test_plan_vehicle_must_match_the_request() -> None:
    plan = luray_weekend_plan()
    plan["vehicle"] = "rv"
    result = check_case(CASES["c04_family_short_hop"], PlannerOutput(plan=plan), CATALOG)
    assert result.checks["vehicle"] is False


def test_missing_overnights_fail_the_structure() -> None:
    plan = luray_weekend_plan()
    plan["days"][0]["overnight"] = None
    plan["totals"] = {"cash_usd": 0, "points": 0, "hotel_nights": 0, "host_nights": 0}
    result = check_case(CASES["c04_family_short_hop"], PlannerOutput(plan=plan), CATALOG)
    assert result.checks["schema"] is False
    assert result.checks["max_drive"] is False


@pytest.mark.parametrize(
    "change",
    [{"origin": "Front Royal, VA"}, {"destination": "Staunton, VA"}, {"days": 3}],
)
def test_plan_must_answer_the_requested_trip(change: dict[str, Any]) -> None:
    case = replace(CASES["c04_family_short_hop"], **change)
    result = check_case(case, PlannerOutput(plan=luray_weekend_plan()), CATALOG)
    assert result.checks["schema"] is False
    assert any(v["code"] == "request_mismatch" for v in result.violations)


def test_endpoint_aliases_match_the_request() -> None:
    plan = luray_weekend_plan()
    plan["origin"], plan["destination"] = "Washington, D.C.", "luray va"
    result = check_case(CASES["c04_family_short_hop"], PlannerOutput(plan=plan), CATALOG)
    assert result.checks["schema"] is True


def test_vague_request_accepts_any_endpoints_and_length() -> None:
    result = check_case(CASES["c10_vague"], PlannerOutput(plan=luray_weekend_plan()), CATALOG)
    assert result.checks["schema"] is True


def test_planner_cannot_rewrite_the_plan_it_replans() -> None:
    base_case = CASES["c04_family_short_hop"]
    replan_case = replace(base_case, id="c99_replan", replan=Replan(of=base_case.id, from_day=2))

    def sneaky(case: EvalCase, previous: dict[str, Any] | None) -> PlannerOutput:
        if previous is None:
            return PlannerOutput(plan=luray_weekend_plan())
        previous["days"][0]["narrative"] = "Rewritten day one. Sneaky."
        return PlannerOutput(plan=previous)

    results = run(sneaky, CATALOG, [base_case, replan_case])
    assert results[1].checks["replan_earlier_days"] is False


def test_first_progress_and_full_plan_latency_are_tracked_separately() -> None:
    def timed(case: EvalCase, previous: dict[str, Any] | None) -> PlannerOutput:
        return PlannerOutput(plan=luray_weekend_plan(), latency_s=30.0, first_progress_s=2.0)

    results = run(timed, CATALOG, [CASES["c04_family_short_hop"]])
    tracked = score([r.checks for r in results], tracked=results)["tracked"]
    assert tracked["first_progress_p50_s"] == 2.0
    assert tracked["latency_p50_s"] == 30.0
