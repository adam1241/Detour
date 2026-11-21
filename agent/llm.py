"""Model clients for the planner loop (ADR-007).

The loop speaks Bedrock Converse-shaped messages and only depends on the `ModelClient`
protocol. Providers: OpenAI Responses API (default, GPT-6 Luna) and Amazon Bedrock Converse.
Tests use a scripted fake.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol


class ModelClient(Protocol):
    model_id: str

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> dict[str, Any]:
        """One Converse call; returns the raw response ({output, stopReason, usage})."""
        ...


class BedrockClient:
    """Bedrock Converse. Configured from the environment:

    DETOUR_MODEL_ID  model or inference-profile ID (required)
    AWS_PROFILE      named profile from `aws configure` (optional)
    AWS_REGION       default us-east-1
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
    ) -> None:
        model_id = model_id or os.environ.get("DETOUR_MODEL_ID")
        if not model_id:
            raise ValueError("set DETOUR_MODEL_ID to a Bedrock model or inference-profile ID")
        self.model_id = model_id
        if client is None:
            import boto3
            from botocore.config import Config

            session = boto3.Session(
                profile_name=profile or os.environ.get("AWS_PROFILE") or None,
                region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
            )
            client = session.client(
                "bedrock-runtime",
                config=Config(read_timeout=120, retries={"max_attempts": 3, "mode": "standard"}),
            )
        self._client = client

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> dict[str, Any]:
        response: dict[str, Any] = self._client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": tools},
            inferenceConfig={"maxTokens": max_tokens},
        )
        return response


# --- OpenAI (ADR-007 amendment, 2026-11-20) ---------------------------------------------------

OPENAI_URL = "https://api.openai.com/v1/responses"
# USD per 1M tokens (input, output), from OpenAI's model page, read 2026-11-20.
OPENAI_PRICES: dict[str, tuple[float, float]] = {"gpt-6-luna": (0.10, 0.50)}

Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


def _http_post(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """POST JSON with two retries on 429/5xx. Errors never include the API key."""
    data = json.dumps(body).encode()
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (fixed https URL)
                result: dict[str, Any] = json.loads(resp.read())
                return result
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:500]
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"OpenAI API error {err.code}: {detail}") from None
    raise RuntimeError("OpenAI API: retries exhausted")


class OpenAIClient:
    """OpenAI Responses API with function calling, behind the same Converse-shaped interface
    the loop uses. Configured from the environment:

    OPENAI_API_KEY            required
    DETOUR_MODEL_ID           default gpt-6-luna
    DETOUR_REASONING_EFFORT   none | low (default) | medium | high
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("set OPENAI_API_KEY to use the OpenAI provider")
        self._key = key
        self.model_id = model_id or os.environ.get("DETOUR_MODEL_ID") or "gpt-6-luna"
        self.reasoning_effort = (
            reasoning_effort or os.environ.get("DETOUR_REASONING_EFFORT") or "low"
        )
        self._post = transport or _http_post

    def cost_usd(self, usage: dict[str, int]) -> float | None:
        price = OPENAI_PRICES.get(self.model_id)
        if price is None:
            return None
        return (
            usage.get("input_tokens", 0) * price[0] + usage.get("output_tokens", 0) * price[1]
        ) / 1e6

    @staticmethod
    def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": t["toolSpec"]["name"],
                "description": t["toolSpec"]["description"],
                "parameters": t["toolSpec"]["inputSchema"]["json"],
            }
            for t in tools
        ]

    @staticmethod
    def _input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "assistant" and "_openai_output" in msg:
                items += msg["_openai_output"]  # reasoning + calls exactly as returned
                continue
            for block in msg.get("content", []):
                if "text" in block:
                    items.append({"role": msg["role"], "content": block["text"]})
                elif "toolUse" in block:
                    use = block["toolUse"]
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": use["toolUseId"],
                            "name": use["name"],
                            "arguments": json.dumps(use.get("input") or {}),
                        }
                    )
                elif "toolResult" in block:
                    res = block["toolResult"]
                    part = res["content"][0]
                    output = json.dumps(part["json"]) if "json" in part else str(part.get("text"))
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": res["toolUseId"],
                            "output": output,
                        }
                    )
        return items

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> dict[str, Any]:
        body = {
            "model": self.model_id,
            "instructions": system,
            "input": self._input(messages),
            "tools": self._tools(tools),
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": self.reasoning_effort},
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        raw = self._post(OPENAI_URL, headers, body)
        output = raw.get("output") or []
        content: list[dict[str, Any]] = []
        for item in output:
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                content.append(
                    {
                        "toolUse": {
                            "toolUseId": item["call_id"],
                            "name": item["name"],
                            "input": args if isinstance(args, dict) else {},
                        }
                    }
                )
            elif item.get("type") == "message":
                content += [
                    {"text": part["text"]}
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                ]
        usage = raw.get("usage") or {}
        return {
            "output": {
                "message": {"role": "assistant", "content": content, "_openai_output": output}
            },
            "stopReason": "tool_use" if any("toolUse" in c for c in content) else "end_turn",
            "usage": {
                "inputTokens": int(usage.get("input_tokens", 0)),
                "outputTokens": int(usage.get("output_tokens", 0)),
            },
        }


def make_model() -> ModelClient:
    """The planner's model from DETOUR_PROVIDER: openai (default) or bedrock."""
    provider = os.environ.get("DETOUR_PROVIDER", "openai")
    if provider == "openai":
        return OpenAIClient()
    if provider == "bedrock":
        return BedrockClient()
    raise ValueError("DETOUR_PROVIDER must be openai or bedrock")
