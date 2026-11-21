"""Planner loop tests with a scripted fake model: no network, no Bedrock, no database."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from agent.llm import BedrockClient
from agent.planner import MAX_TURNS, TripRequest, _progress_text, plan_trip
from agent.tools.registry import MemorySpatial, ToolBox, tool_specs
from evals.catalog import seed_catalog

CATALOG = seed_catalog()
SPATIAL = MemorySpatial(CATALOG.all())

Step = dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]


class FakeModel:
    """Replays scripted Converse responses and records every request."""

    model_id = "fake-model"

    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.calls: list[list[dict[str, Any]]] = []

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> dict[str, Any]:
        assert system and tools
        self.calls.append(copy.deepcopy(messages))
        if not self.steps:
            raise AssertionError("model called more often than scripted")
        step = self.steps.pop(0)
        content = step(messages) if callable(step) else step
        return {
            "output": {"message": {"role": "assistant", "content": content["content"]}},
            "stopReason": "tool_use"
            if any("toolUse" in c for c in content["content"])
            else "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }


def tool(name: str, args: dict[str, Any], uid: str = "t1") -> dict[str, Any]:
    return {"content": [{"toolUse": {"toolUseId": uid, "name": name, "input": args}}]}


def text(msg: str) -> dict[str, Any]:
    return {"content": [{"text": msg}]}


def tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c["toolResult"] for c in messages[-1]["content"] if "toolResult" in c]


def luray_plan(**overrides: Any) -> dict[str, Any]:
    """DC to Staunton in 2 days by car, one night at a Luray hotel. Numbers left at 0: the
    planner fills drive times, distances and totals from the tools."""
    plan: dict[str, Any] = {
        "summary": "Two easy days down the Shenandoah Valley.",
        "origin": "Washington, DC",
        "destination": "Staunton, VA",
        "vehicle": "car",
        "days": [
            {
                "day": 1,
                "drive_minutes": 0,
                "distance_km": 0,
                "stops": [],
                "overnight": {
                    "kind": "hotel",
                    "id": "hotel_quality_inn_luray",
                    "reason_code": "drive_time",
                    "reason": "Luray splits the drive into two short days.",
                },
                "narrative": "Head west out of DC into the hills. Settle in at Luray tonight.",
            },
            {
                "day": 2,
                "drive_minutes": 0,
                "distance_km": 0,
                "stops": [],
                "overnight": None,
                "narrative": "Roll south through the valley. Arrive in Staunton by lunch.",
            },
        ],
        "warnings": [],
        "alternatives": [],
    }
    plan.update(overrides)
    return plan


def request(**kw: Any) -> TripRequest:
    return TripRequest(text="DC to Staunton, 2 days, by car.", vehicle="car", **kw)


HOTELS_NEAR_LURAY = tool("find_hotels_near", {"near": "Luray, VA", "radius_km": 15}, "h1")


def run(steps: list[Step], req: TripRequest | None = None, **kw: Any) -> Any:
    model = FakeModel(steps)
    result = plan_trip(req or request(), model=model, catalog=CATALOG, spatial=SPATIAL, **kw)
    return result, model


def test_tool_results_go_back_to_the_model() -> None:
    def check(messages: list[dict[str, Any]]) -> dict[str, Any]:
        (result,) = tool_results(messages)
        assert result["toolUseId"] == "r1"
        body = result["content"][0]["json"]
        assert body["distance_km"] > 0 and "geometry" not in body  # no huge polyline
        return HOTELS_NEAR_LURAY

    result, _ = run(
        [
            tool("get_route", {"origin": "DC", "destination": "Staunton"}, "r1"),
            check,
            tool("validate_plan", {"plan": luray_plan()}, "v1"),
        ]
    )
    assert result.error is None


def test_a_clean_validation_returns_the_plan_with_numbers_from_the_tools() -> None:
    result, model = run([HOTELS_NEAR_LURAY, tool("validate_plan", {"plan": luray_plan()}, "v1")])
    assert result.error is None and result.violations == []
    assert len(model.calls) == 2  # no extra model turn after the plan passes
    day1, day2 = result.plan["days"]
    assert day1["drive_minutes"] > 60 and day2["drive_minutes"] > 30
    assert day1["distance_km"] > 100
    assert result.plan["totals"]["hotel_nights"] == 1
    assert result.plan["totals"]["cash_usd"] > 0
    assert result.plan["trip_id"]
    assert "hotel_quality_inn_luray" in result.allowed_ids


def test_an_id_no_tool_returned_is_sent_back_for_repair() -> None:
    def repair(messages: list[dict[str, Any]]) -> dict[str, Any]:
        (result,) = tool_results(messages)
        body = result["content"][0]["json"]
        assert body["valid"] is False
        assert "not_from_tools" in {v["code"] for v in body["violations"]}
        return HOTELS_NEAR_LURAY

    result, _ = run(
        [
            tool("validate_plan", {"plan": luray_plan()}, "v1"),  # no search yet
            repair,
            tool("validate_plan", {"plan": luray_plan()}, "v2"),
        ]
    )
    assert result.error is None


def test_tool_errors_are_reported_to_the_model_not_raised() -> None:
    def check(messages: list[dict[str, Any]]) -> dict[str, Any]:
        (result,) = tool_results(messages)
        assert result["status"] == "error"
        assert result["content"][0]["json"]["error"] == "outside_coverage"
        return HOTELS_NEAR_LURAY

    result, _ = run(
        [
            tool("get_route", {"origin": "Chicago", "destination": "Staunton"}, "r1"),
            check,
            tool("validate_plan", {"plan": luray_plan()}, "v1"),
        ]
    )
    assert result.error is None


def test_unknown_tools_and_bad_arguments_are_errors_for_the_model() -> None:
    box = ToolBox(CATALOG, SPATIAL, request())
    body, ok = box.call("book_hotel", {})
    assert not ok and body["error"] == "unknown_tool"
    body, ok = box.call("get_route", {"origin": "DC"})
    assert not ok and body["error"] == "bad_input"


def test_the_loop_stops_at_the_turn_limit_without_a_plan() -> None:
    steps: list[Step] = [
        tool("get_route", {"origin": "DC", "destination": "Luray"}, f"r{i}")
        for i in range(MAX_TURNS + 5)
    ]
    result, model = run(steps)
    assert result.plan is None and result.error == "turn_limit"
    assert len(model.calls) == MAX_TURNS


def test_repeated_invalid_plans_end_in_a_structured_failure() -> None:
    bad = luray_plan(destination="Chicago")
    steps: list[Step] = [tool("validate_plan", {"plan": bad}, f"v{i}") for i in range(MAX_TURNS)]
    result, _ = run([HOTELS_NEAR_LURAY, *steps])
    assert result.plan is None and result.error == "plan_invalid"
    assert result.violations  # the last violations, for the caller to show


def test_a_text_answer_is_nudged_to_validate_then_fails() -> None:
    def nudged(messages: list[dict[str, Any]]) -> dict[str, Any]:
        assert "validate_plan" in messages[-1]["content"][0]["text"]
        return text("Here is your plan!")

    result, _ = run([text("Here is your plan!"), nudged])
    assert result.plan is None and result.error == "no_plan"


def test_first_progress_event_comes_before_the_first_model_call() -> None:
    events: list[dict[str, Any]] = []

    class Spy(FakeModel):
        def converse(self, **kw: Any) -> dict[str, Any]:
            assert events, "progress must be sent before the model is called"
            return super().converse(**kw)

    model = Spy([HOTELS_NEAR_LURAY, tool("validate_plan", {"plan": luray_plan()}, "v1")])
    plan_trip(request(), model=model, catalog=CATALOG, spatial=SPATIAL, on_progress=events.append)
    assert events[0]["type"] == "progress"
    assert any("hotels" in e["message"].lower() for e in events)


def test_trace_records_tool_calls_and_tokens() -> None:
    result, _ = run([HOTELS_NEAR_LURAY, tool("validate_plan", {"plan": luray_plan()}, "v1")])
    assert [t["tool"] for t in result.trace] == ["find_hotels_near", "validate_plan"]
    assert all(t["ok"] and t["latency_ms"] >= 0 for t in result.trace)
    assert result.usage == {"input_tokens": 200, "output_tokens": 100}
    assert result.model == "fake-model" and result.prompt_version == "planner.v1"


def test_request_vehicle_wins_over_the_drafted_one() -> None:
    result, _ = run(
        [HOTELS_NEAR_LURAY, tool("validate_plan", {"plan": luray_plan(vehicle="rv")}, "v1")]
    )
    assert result.plan["vehicle"] == "car"


def test_a_replan_keeps_the_earlier_days_exactly() -> None:
    first, _ = run([HOTELS_NEAR_LURAY, tool("validate_plan", {"plan": luray_plan()}, "v1")])
    previous = first.plan
    edited = luray_plan()
    edited["days"][0]["narrative"] = "A different first day. It should not survive the replan."
    edited["days"][1]["narrative"] = "A calmer last day. Staunton by mid-afternoon."
    result, model = run(
        [HOTELS_NEAR_LURAY, tool("validate_plan", {"plan": edited}, "v1")],
        request(previous_plan=previous, replan_from_day=2),
    )
    assert result.error is None
    assert result.plan["days"][0] == previous["days"][0]
    assert result.plan["days"][1]["narrative"].startswith("A calmer")
    assert "Replan from day 2" in model.calls[0][0]["content"][0]["text"]


def test_budget_from_the_request_is_enforced() -> None:
    result, _ = run(
        [
            HOTELS_NEAR_LURAY,
            *[tool("validate_plan", {"plan": luray_plan()}, f"v{i}") for i in range(MAX_TURNS)],
        ],
        request(budget_usd=10),
    )
    assert result.error == "plan_invalid"
    assert "budget" in {v["code"] for v in result.violations}


def test_memory_spatial_finds_stops_in_route_order() -> None:
    box = ToolBox(CATALOG, SPATIAL, request())
    body, ok = box.call(
        "find_stops_along_route",
        {"origin": "DC", "destination": "Asheville", "types": ["winery"], "max_detour_km": 30},
    )
    assert ok
    positions = [s["position_along_route"] for s in body["stops"]]
    assert positions == sorted(positions) and len(positions) >= 2
    assert {s["type"] for s in body["stops"]} == {"winery"}
    assert all(s["id"] in box.allowed_ids for s in body["stops"])


def test_tool_specs_cover_the_five_tools() -> None:
    names = [t["toolSpec"]["name"] for t in tool_specs()]
    assert names == [
        "get_route",
        "find_stops_along_route",
        "find_hotels_near",
        "calculate_trip_cost",
        "validate_plan",
    ]
    assert all(t["toolSpec"]["inputSchema"]["json"]["type"] == "object" for t in tool_specs())


def test_bedrock_client_sends_a_converse_request() -> None:
    sent: dict[str, Any] = {}

    class Boto:
        def converse(self, **kw: Any) -> dict[str, Any]:
            sent.update(kw)
            return {"output": {"message": {"role": "assistant", "content": []}}}

    client = BedrockClient(model_id="m-1", client=Boto())
    client.converse(system="sys", messages=[], tools=tool_specs(), max_tokens=100)
    assert sent["modelId"] == "m-1"
    assert sent["system"] == [{"text": "sys"}]
    assert sent["toolConfig"]["tools"] == tool_specs()
    assert sent["inferenceConfig"]["maxTokens"] == 100


def test_bedrock_client_needs_a_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DETOUR_MODEL_ID", raising=False)
    with pytest.raises(ValueError, match="DETOUR_MODEL_ID"):
        BedrockClient(client=object())


def test_progress_text_pluralizes_stop_types() -> None:
    text = _progress_text("find_stops_along_route", {"types": ["winery", "farm", "brewery"]})
    assert text == "Looking for wineries, farms, breweries along the way"
