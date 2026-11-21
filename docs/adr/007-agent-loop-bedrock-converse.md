# ADR-007: Agent loop on the Bedrock Converse API (amends ADR-001)

- **Status:** Accepted by Adam, 2026-11-20
- **Spec refs:** §5.1, §5.3, §7, §10 (Day 2 cut-line)
- **Amends:** ADR-001 (framework only; language, repo layout and the shared JSON Schema stand)

## Context
ADR-001 picked the Strands Agents SDK for the planner. On the final day the agent was not yet
built, AWS access was still being set up, and the loop had to be testable offline (CI has no
AWS credentials) and quick to debug. The planner needs only a small part of an agent framework:
one model, five tools, and a loop that ends when `validate_plan` passes.

## Options
### A. Strands Agents SDK (ADR-001)
- ➕ Less loop code; built-in OTel spans; documented path onto AgentCore Runtime.
- ➖ A heavier dependency tree; offline tests mean mocking Strands' model interface; the
  "return only through validate_plan" rule has to be fitted into its stop conditions.

### B. Our own loop on the Bedrock Converse API (`boto3`)
- ➕ About 150 lines we own: the stop rule ("first plan that passes validate_plan"), repair
  limits and progress events are explicit.
- ➕ Fully testable offline with a scripted fake client (17 tests, no network).
- ➕ One dependency (`boto3`). Another provider is one class behind `ModelClient`.
- ➖ No built-in OTel; tracing is our `trips.trace_json` row (ADR-004 already plans that).
- ➖ We maintain the loop.

## Decision
**Option B.** `agent/llm.py` holds `ModelClient` and `BedrockClient`, configured by
`DETOUR_MODEL_ID`, `AWS_PROFILE` and `AWS_REGION` (default `us-east-1`). `agent/planner.py`
runs the loop:
- The model drafts the plan's structure. Drive times, distances and totals are filled in by
  code from `get_route`, side-trip estimates and `calculate_trip_cost`, the same functions
  `validate_plan` checks, so the model never writes those numbers.
- The plan is returned only when `validate_plan` (with `allowed_ids` = IDs the tools returned
  this run) has no blocking violations. Limits: 12 model turns, 4 validations. Otherwise the
  result is a structured failure (`turn_limit`, `plan_invalid`, `no_plan`), never a bad plan.
- Inputs the request fixes are applied by code: the vehicle, and on a replan the days before
  the replanned day are copied from the previous plan.
- Route geometry never passes through the model: tools take town names or `[lon, lat]` points.

## Consequences
- AgentCore stays possible (it hosts plain Python), but today's cut-line runs the agent locally.
- Tracing: tool name, ok, latency per call, tokens per run, `model`, `prompt_version` go in the
  `trips` row. Cost per plan stays empty until the Bedrock price for the model is confirmed.
- Tests: `agent/tests/test_planner.py` covers the loop with a fake model; live behaviour is
  measured by the eval suite (`evals.runner --planner agent.planner:plan_for_eval`).

## Amendment (2026-11-20): OpenAI GPT-6 Luna as the default model
- **Context:** the AWS account returns "not available for this account" for Anthropic models on
  Bedrock, an account-level block that needs an AWS Support case (days). The demo is today.
- **Options:** wait for AWS Support; the Anthropic API directly; the OpenAI API.
- **Decision (Adam):** OpenAI, model `gpt-6-luna` (OpenAI's model page lists function calling;
  the Responses API is recommended for it). `OpenAIClient` in `agent/llm.py` calls
  `POST /v1/responses` over plain HTTPS (no new dependency) and maps it to and from the
  Converse-shaped messages, so the loop is unchanged. Reasoning items are replayed between
  turns. `DETOUR_REASONING_EFFORT` defaults to `low` for latency.
  `DETOUR_PROVIDER=bedrock` still selects `BedrockClient`.
- **Consequences:** key `OPENAI_API_KEY` in the local `.env` (never committed). Cost per plan is
  now tracked from the published price ($0.10 / $0.50 per 1M input / output tokens, read
  2026-11-20). The prompt was written for Claude; eval results decide whether it needs a v2.
