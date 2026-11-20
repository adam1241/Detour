"""Model client for the planner loop (ADR-007): Claude on Amazon Bedrock via the Converse API.

The loop only depends on the `ModelClient` protocol, so tests use a scripted fake and another
provider would be one more class here.
"""

from __future__ import annotations

import os
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
