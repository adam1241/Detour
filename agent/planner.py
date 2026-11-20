"""The trip planner: Claude plus five tools in a Converse tool-use loop (spec §5, ADR-007).

The model drafts a plan from tool results and must call validate_plan; the first plan that
passes (no blocking violations) is returned, numbers filled from the tools. Nothing invalid
is ever returned: the loop ends in a structured failure instead.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from agent.llm import BedrockClient, ModelClient
from agent.plan.catalog import Catalog
from agent.request import TripRequest
from agent.tools.registry import Spatial, ToolBox, timed_call, tool_specs

__all__ = ["MAX_TURNS", "PlanResult", "TripRequest", "plan_for_eval", "plan_trip"]

PROMPT_VERSION = "planner.v1"
PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / f"{PROMPT_VERSION}.md"
MAX_TURNS = 12  # model calls per run
MAX_VALIDATIONS = 4  # first draft + 3 repair rounds
MAX_TOKENS = 8000
NUDGE = (
    "Your plan is only returned through the validate_plan tool. Call validate_plan with the "
    "complete plan JSON now."
)

Progress = Callable[[dict[str, Any]], None]


@dataclass
class PlanResult:
    plan: dict[str, Any] | None
    error: str | None = None  # turn_limit | plan_invalid | no_plan
    violations: list[dict[str, Any]] = field(default_factory=list)
    allowed_ids: set[str] = field(default_factory=set)
    trace: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    turns: int = 0
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    first_progress_s: float | None = None
    latency_s: float | None = None


@cache
def system_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _user_message(request: TripRequest) -> str:
    lines = [f"Trip request: {request.text}", "", "Constraints:"]
    if request.vehicle:
        lines.append(f"- vehicle: {request.vehicle}")
    lines.append(f"- max driving per day: {request.max_drive_minutes:.0f} minutes")
    if request.budget_usd is not None:
        lines.append(f"- lodging budget: ${request.budget()} total")
    if request.previous_plan is not None and request.replan_from_day is not None:
        n = request.replan_from_day
        lines += [
            "",
            f"Replan from day {n}. Days before day {n} stay exactly as they are (they are "
            f"copied over automatically). Change day {n} onward to satisfy the request.",
            "Previous plan:",
            json.dumps(request.previous_plan, default=str),
        ]
    return "\n".join(lines)


def _progress_text(name: str, args: dict[str, Any]) -> str:
    def place(v: Any) -> str:
        return v if isinstance(v, str) else "a point on the route"

    if name == "get_route":
        return f"Checking the drive from {place(args.get('origin'))} to " + place(
            args.get("destination")
        )
    if name == "find_stops_along_route":
        kinds = ", ".join(f"{t}s" for t in args.get("types") or []) or "stops"
        return f"Looking for {kinds} along the way"
    if name == "find_hotels_near":
        return f"Looking for hotels near {place(args.get('near'))}"
    if name == "calculate_trip_cost":
        return "Adding up lodging costs"
    if name == "validate_plan":
        return "Checking the plan against drive time, budget and route rules"
    return f"Running {name}"


def plan_trip(
    request: TripRequest,
    *,
    model: ModelClient,
    catalog: Catalog,
    spatial: Spatial,
    on_progress: Progress | None = None,
) -> PlanResult:
    started = time.perf_counter()
    result = PlanResult(plan=None, model=model.model_id)

    def emit(message: str, **extra: Any) -> None:
        if result.first_progress_s is None:
            result.first_progress_s = time.perf_counter() - started
        if on_progress is not None:
            on_progress({"type": "progress", "message": message, **extra})

    emit("Reading your trip request")
    box = ToolBox(catalog, spatial, request)
    tools = tool_specs()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": _user_message(request)}]}
    ]
    nudged = False

    def finish(error: str | None) -> PlanResult:
        result.error = error
        result.allowed_ids = set(box.allowed_ids)
        result.violations = [] if error is None else box.last_violations
        result.latency_s = time.perf_counter() - started
        return result

    while result.turns < MAX_TURNS:
        result.turns += 1
        response = model.converse(
            system=system_prompt(), messages=messages, tools=tools, max_tokens=MAX_TOKENS
        )
        usage = response.get("usage") or {}
        result.usage["input_tokens"] += int(usage.get("inputTokens", 0))
        result.usage["output_tokens"] += int(usage.get("outputTokens", 0))
        message = response["output"]["message"]
        messages.append(message)
        uses = [c["toolUse"] for c in message.get("content", []) if "toolUse" in c]

        if not uses:
            if nudged:
                return finish("no_plan")
            nudged = True
            messages.append({"role": "user", "content": [{"text": NUDGE}]})
            continue

        results = []
        for use in uses:
            args = use.get("input") or {}
            emit(_progress_text(use["name"], args), tool=use["name"])
            body, ok, ms = timed_call(box, use["name"], args)
            result.trace.append(
                {"tool": use["name"], "ok": ok, "latency_ms": ms, "turn": result.turns}
            )
            block: dict[str, Any] = {"toolUseId": use["toolUseId"], "content": [{"json": body}]}
            if not ok:
                block["status"] = "error"
            results.append({"toolResult": block})
            if box.accepted is not None:
                plan = box.accepted
                plan["trip_id"] = str(uuid.uuid4())
                result.plan = plan
                emit("Plan ready")
                return finish(None)
        if box.validations >= MAX_VALIDATIONS:
            return finish("plan_invalid")
        messages.append({"role": "user", "content": results})

    return finish("turn_limit")


# --- Eval adapter: evals.runner --planner agent.planner:plan_for_eval ------------------------


@cache
def _default_model() -> ModelClient:
    return BedrockClient()


@cache
def default_backends() -> tuple[Catalog, Spatial]:
    """PostGIS when DATABASE_URL is set, else the seed CSVs in memory."""
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg

        from agent.plan.catalog import DbCatalog
        from agent.tools.registry import DbSpatial

        conn = psycopg.connect(url, autocommit=True, connect_timeout=10)
        return DbCatalog(conn), DbSpatial(conn)
    from agent.tools.registry import MemorySpatial
    from evals.catalog import seed_catalog

    catalog = seed_catalog()
    return catalog, MemorySpatial(catalog.all())


def plan_for_eval(case: Any, previous: dict[str, Any] | None) -> Any:
    from evals.runner import PlannerOutput

    catalog, spatial = default_backends()
    request = TripRequest(
        text=case.request,
        vehicle=case.vehicle,
        max_drive_minutes=case.max_drive_minutes,
        budget_usd=case.budget_usd,
        previous_plan=previous,
        replan_from_day=case.replan.from_day if case.replan else None,
    )
    out = plan_trip(request, model=_default_model(), catalog=catalog, spatial=spatial)
    return PlannerOutput(
        plan=out.plan,
        allowed_ids=out.allowed_ids,
        latency_s=out.latency_s,
        first_progress_s=out.first_progress_s,
        cost_usd=None,  # set once the Bedrock price for the model is confirmed
        error=out.error,
    )
