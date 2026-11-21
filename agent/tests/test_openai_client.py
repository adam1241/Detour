"""OpenAI Responses client: converts the loop's Converse-shaped turns both ways. No network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent.llm import OpenAIClient, make_model
from agent.tools.registry import tool_specs


class Transport:
    """Records the request body and returns scripted Responses API bodies."""

    def __init__(self, *bodies: dict[str, Any]) -> None:
        self.bodies = list(bodies)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        assert url == "https://api.openai.com/v1/responses"
        assert headers["Authorization"] == "Bearer sk-test"
        self.sent.append(json.loads(json.dumps(body)))
        return self.bodies.pop(0)


CALL: dict[str, Any] = {
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "get_route",
            "arguments": '{"origin": "DC", "destination": "Luray"}',
        },
    ],
    "usage": {"input_tokens": 1000, "output_tokens": 200},
}
TEXT: dict[str, Any] = {
    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Done."}]}],
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


def client(transport: Transport) -> OpenAIClient:
    return OpenAIClient(api_key="sk-test", model_id="gpt-6-luna", transport=transport)


def test_request_uses_responses_api_with_function_tools() -> None:
    t = Transport(CALL)
    client(t).converse(
        system="sys",
        messages=[{"role": "user", "content": [{"text": "plan it"}]}],
        tools=tool_specs(),
        max_tokens=500,
    )
    body = t.sent[0]
    assert body["model"] == "gpt-6-luna"
    assert body["instructions"] == "sys"
    assert body["max_output_tokens"] == 500
    assert body["reasoning"] == {"effort": "low"}
    assert body["input"] == [{"role": "user", "content": "plan it"}]
    first = body["tools"][0]
    assert first["type"] == "function" and first["name"] == "get_route"
    assert first["parameters"]["type"] == "object"


def test_function_calls_come_back_as_tool_uses_with_usage() -> None:
    out = client(Transport(CALL)).converse(system="s", messages=[], tools=[], max_tokens=10)
    (block,) = out["output"]["message"]["content"]
    assert block["toolUse"] == {
        "toolUseId": "call_1",
        "name": "get_route",
        "input": {"origin": "DC", "destination": "Luray"},
    }
    assert out["usage"] == {"inputTokens": 1000, "outputTokens": 200}
    assert out["stopReason"] == "tool_use"


def test_text_comes_back_as_text() -> None:
    out = client(Transport(TEXT)).converse(system="s", messages=[], tools=[], max_tokens=10)
    assert out["output"]["message"]["content"] == [{"text": "Done."}]
    assert out["stopReason"] == "end_turn"


def test_history_replays_reasoning_and_calls_then_tool_outputs() -> None:
    t = Transport(CALL, TEXT)
    c = client(t)
    messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": "go"}]}]
    first = c.converse(system="s", messages=messages, tools=[], max_tokens=10)
    messages.append(first["output"]["message"])
    messages.append(
        {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "call_1", "content": [{"json": {"km": 1}}]}}],
        }
    )
    c.converse(system="s", messages=messages, tools=[], max_tokens=10)
    replay = t.sent[1]["input"]
    assert replay[0] == {"role": "user", "content": "go"}
    assert replay[1:3] == CALL["output"]  # reasoning + call, exactly as returned
    assert replay[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": json.dumps({"km": 1}),
    }


def test_bad_tool_arguments_become_an_empty_input() -> None:
    bad = {"output": [{**CALL["output"][1], "arguments": "{not json"}], "usage": {}}
    out = client(Transport(bad)).converse(system="s", messages=[], tools=[], max_tokens=10)
    assert out["output"]["message"]["content"][0]["toolUse"]["input"] == {}


def test_cost_uses_the_published_price() -> None:
    c = client(Transport())
    # gpt-6-luna: $0.10 / 1M input, $0.50 / 1M output
    assert c.cost_usd({"input_tokens": 1_000_000, "output_tokens": 1_000_000}) == pytest.approx(0.6)


def test_needs_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIClient(transport=Transport())


def test_provider_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("DETOUR_PROVIDER", raising=False)
    assert isinstance(make_model(), OpenAIClient)
    monkeypatch.setenv("DETOUR_PROVIDER", "nope")
    with pytest.raises(ValueError, match="DETOUR_PROVIDER"):
        make_model()
