"""Demo server, hydration and the sample plan: no network, no AWS, no database."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from agent.hydrate import hydrate
from agent.plan.validate import validate_plan
from agent.server import SAMPLE_PLAN, RateLimiter, SampleModel, make_handler, parse_request
from evals.catalog import seed_catalog

CATALOG = seed_catalog()


def sample() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(SAMPLE_PLAN.read_text(encoding="utf-8"))
    return plan


def test_the_sample_plan_is_valid() -> None:
    assert validate_plan(sample(), CATALOG, max_drive_minutes=300) == []


def test_hydrate_adds_names_points_and_one_line_per_day() -> None:
    out = hydrate(sample(), CATALOG)
    assert (
        out["places"]["hotel_quality_inn_roanoke_airport"]["name"] == "Quality Inn Roanoke Airport"
    )
    assert out["places"]["stop_shipley_farms"]["kind"] == "stop"
    features = out["route_geojson"]["features"]
    assert [f["properties"]["day"] for f in features] == [1, 2, 3, 4]
    assert all(len(f["geometry"]["coordinates"]) > 10 for f in features)
    json.dumps(out)  # JSON-ready


def test_rate_limit_is_per_ip_per_window() -> None:
    limiter = RateLimiter(limit=2, window_s=10)
    assert limiter.allow("a", 0) and limiter.allow("a", 1)
    assert not limiter.allow("a", 2)
    assert limiter.allow("b", 2)
    assert limiter.allow("a", 10.5)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": "x" * 1001},
        {"text": "trip", "vehicle": "boat"},
        {"text": "trip", "max_drive_minutes": 5},
        {"text": "trip", "budget_usd": -1},
        {"text": "trip", "replan_from_day": 2},
    ],
)
def test_bad_requests_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        parse_request(body)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("agent.server.time.sleep", lambda _s: None)
    plan = sample()
    handler = make_handler(lambda: SampleModel(plan), RateLimiter(limit=2))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post(url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    req = urllib.request.Request(  # noqa: S310 (local test server)
        url + "/api/plan",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (local test server)
        assert resp.headers["Content-Type"] == "text/event-stream"
        lines = resp.read().decode().split("\n\n")
    return [json.loads(line[len("data: ") :]) for line in lines if line.startswith("data: ")]


def test_plan_endpoint_streams_progress_then_a_hydrated_plan(server: str) -> None:
    events = post(server, {"text": "DC to Asheville in 4 days by RV", "vehicle": "rv"})
    assert events[0]["type"] == "progress"
    final = events[-1]
    assert final["type"] == "plan"
    assert final["plan"]["totals"]["hotel_nights"] == 1
    assert len(final["route_geojson"]["features"]) == 4
    assert "stop_dices_spring_farm" in final["places"]


def test_rate_limited_requests_get_429(server: str) -> None:
    body = {"text": "DC to Asheville", "vehicle": "rv"}
    post(server, body)
    post(server, body)
    with pytest.raises(urllib.error.HTTPError) as err:
        post(server, body)
    assert err.value.code == 429


def test_page_is_served(server: str) -> None:
    with urllib.request.urlopen(server + "/", timeout=10) as resp:  # noqa: S310
        assert b"Detour" in resp.read()
