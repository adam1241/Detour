"""Local demo server (final-day cut-line: no Vercel/AgentCore; spec v0.4).

    uv run python -m agent.server [--sample] [--port 8000]

GET  /           the demo page (apps/demo/index.html)
POST /api/plan   {text, vehicle?, budget_usd?, max_drive_minutes?, previous_plan?,
                  replan_from_day?} -> Server-Sent Events: progress..., then plan or error

--sample replays a fixed, validated plan through the same loop and validation (no AWS).
Live mode needs OPENAI_API_KEY (default provider) or DETOUR_PROVIDER=bedrock with AWS
credentials (ADR-007). Each run is saved to `trips`
when DATABASE_URL is set. Rate limit: 10 plans per IP per hour (spec §6).
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent.hydrate import hydrate
from agent.llm import ModelClient, make_model
from agent.planner import PlanResult, TripRequest, default_backends, plan_trip

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "apps" / "demo" / "index.html"
SAMPLE_PLAN = ROOT / "apps" / "demo" / "sample_plan.json"
RATE_LIMIT, RATE_WINDOW_S = 10, 3600
MAX_BODY = 200_000


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window_s: float = RATE_WINDOW_S) -> None:
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class SampleModel:
    """Scripted model for --sample: searches like the real agent, then submits the sample plan
    (adjusted to the request's replan) through validate_plan."""

    model_id = "sample"

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.step = 0

    def converse(self, **_: Any) -> dict[str, Any]:
        steps = [
            ("get_route", {"origin": self.plan["origin"], "destination": self.plan["destination"]}),
            (
                "find_stops_along_route",
                {
                    "origin": self.plan["origin"],
                    "destination": self.plan["destination"],
                    "types": ["winery", "farm", "brewery", "museum"],
                },
            ),
            ("find_hotels_near", {"near": "Roanoke, VA", "radius_km": 25}),
            ("validate_plan", {"plan": self.plan}),
        ]
        name, args = steps[min(self.step, len(steps) - 1)]
        self.step += 1
        time.sleep(0.4)  # paces the demo's progress messages
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"toolUse": {"toolUseId": f"s{self.step}", "name": name, "input": args}}
                    ],
                }
            },
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }


def parse_request(body: dict[str, Any]) -> TripRequest:
    text = str(body.get("text") or "").strip()
    if not text or len(text) > 1000:
        raise ValueError("text is required (at most 1000 characters)")
    vehicle = body.get("vehicle")
    if vehicle not in (None, "rv", "car"):
        raise ValueError("vehicle must be rv or car")
    max_drive = float(body.get("max_drive_minutes") or 300)
    if not 60 <= max_drive <= 720:
        raise ValueError("max_drive_minutes must be between 60 and 720")
    budget = body.get("budget_usd")
    if budget is not None and float(budget) <= 0:
        raise ValueError("budget_usd must be positive")
    previous = body.get("previous_plan")
    from_day = body.get("replan_from_day")
    if (previous is None) != (from_day is None):
        raise ValueError("a replan needs both previous_plan and replan_from_day")
    return TripRequest(
        text=text,
        vehicle=vehicle,
        max_drive_minutes=max_drive,
        budget_usd=None if budget is None else float(budget),
        previous_plan=previous,
        replan_from_day=None if from_day is None else int(from_day),
    )


def save_trip(request: TripRequest, result: PlanResult) -> None:
    """One trips row per run (ADR-004), when a database is configured. Best effort."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return
    import psycopg
    from psycopg.types.json import Jsonb

    trace = {
        "tools": result.trace,
        "usage": result.usage,
        "turns": result.turns,
        "error": result.error,
        "violations": result.violations,
        "first_progress_s": result.first_progress_s,
        "latency_s": result.latency_s,
        "cost_usd": result.cost_usd,
    }
    vehicle = request.vehicle or (result.plan or {}).get("vehicle") or "car"
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            conn.execute(
                "INSERT INTO trips (request_text, vehicle, plan_json, trace_json, model, "
                "prompt_version) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    request.text,
                    vehicle,
                    Jsonb(result.plan),
                    Jsonb(trace),
                    result.model,
                    result.prompt_version,
                ),
            )
    except psycopg.Error as err:
        print(f"trips row not saved: {err}")


def make_handler(model_factory: Any, limiter: RateLimiter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} {fmt % args}")

        def _json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path not in ("/", "/index.html"):
                self._json(404, {"error": "not_found"})
                return
            data = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/api/plan":
                self._json(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json(413, {"error": "too_large"})
                return
            try:
                request = parse_request(json.loads(self.rfile.read(length) or b"{}"))
            except (ValueError, TypeError) as err:
                self._json(400, {"error": "bad_request", "message": str(err)})
                return
            if not limiter.allow(self.client_address[0]):
                self._json(429, {"error": "rate_limited", "message": "10 plans per hour"})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def send(event: dict[str, Any]) -> None:
                self.wfile.write(f"data: {json.dumps(event, default=str)}\n\n".encode())
                self.wfile.flush()

            catalog, spatial = default_backends()
            try:
                result = plan_trip(
                    request,
                    model=model_factory(),
                    catalog=catalog,
                    spatial=spatial,
                    on_progress=send,
                )
            except Exception as err:  # model/network failure: tell the page, keep serving
                send({"type": "error", "error": "agent_failed", "message": str(err)[:300]})
                return
            save_trip(request, result)
            if result.plan is None:
                send(
                    {
                        "type": "error",
                        "error": result.error,
                        "message": "No valid plan could be built for this request.",
                        "violations": result.violations,
                    }
                )
                return
            send({"type": "plan", **hydrate(result.plan, catalog), "latency_s": result.latency_s})

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true", help="no AWS: replay the sample plan")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    factory: Any
    if args.sample:
        plan = json.loads(SAMPLE_PLAN.read_text(encoding="utf-8"))

        def factory() -> ModelClient:
            return SampleModel(plan)
    else:
        client = make_model()

        def factory() -> ModelClient:
            return client

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(factory, RateLimiter()))
    print(f"Detour demo on http://127.0.0.1:{args.port} ({'sample' if args.sample else 'live'})")
    server.serve_forever()


if __name__ == "__main__":
    main()
