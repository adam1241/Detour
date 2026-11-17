# ADR-001: Agent stack and repo layout

- **Status:** Accepted by Adam, 2026-11-17
- **Spec refs:** §5.3, §7, §9, §10 (Day 2 cut-line)

## Context

The planner is a tool-using Claude agent that must run on AWS Bedrock AgentCore (spec §7), with a local fallback if AgentCore setup takes more than 2h (spec §10, §11). The web app is Next.js + TypeScript (CLAUDE.md layout). We need to choose the agent's language and framework, and how the repo is laid out, before writing any code.

Forces:
- **4-day timebox.** Every extra toolchain costs setup time in CI, pre-commit and hooks.
- **Deterministic logic must be tested first** (spatial queries, cost math, validation). That code lives with the agent tools.
- **Plan schema is shared** between agent (produces it), evals (checks it) and web (renders it). Two languages means two copies of the schema unless we generate one from the other.
- **AgentCore fallback**: the same agent must run locally / on Lambda with no code changes.
- **Tracing**: every run must log tool calls, latency, tokens, cost, prompt_version (spec §7).

## Options

### A. Python agent (Strands Agents SDK) + TypeScript web, monorepo
- ➕ Strands is AWS's own agent SDK and the best-documented path onto AgentCore Runtime; AgentCore samples are mostly Python.
- ➕ Python has the best geo tooling for tests and seeding (`psycopg`, `shapely`, `pytest`), and eval runners are easy to write in `pytest`.
- ➕ Strands is model-agnostic and runs the same code locally, which gives us the Day 2 fallback for free.
- ➖ Two toolchains: `uv`/ruff/mypy/pytest **and** pnpm/eslint/prettier/tsc. Roughly double CI and pre-commit config.
- ➖ Plan schema must be shared across languages. Mitigation: **JSON Schema is the single source of truth** (`packages/schema/plan.schema.json`); Python validates with `jsonschema`/Pydantic, TS generates types with `json-schema-to-typescript`.

### B. TypeScript everywhere (Strands TS or plain Anthropic/Bedrock SDK) + Next.js
- ➕ One toolchain, one linter, shared types with zero codegen.
- ➕ Fewer moving parts in CI; faster Day 1.
- ➖ AgentCore TS support and the Strands TS SDK are newer and have fewer examples (verify current state before choosing). Higher risk of burning the 2h AgentCore spike.
- ➖ Weaker geo/test ecosystem for seed scripts (workable with `pg` + `@turf/turf`).

### C. TypeScript agent inside Next.js route handlers (no AgentCore)
- ➕ Simplest possible deploy: one Vercel app.
- ➖ **Contradicts spec §7** (AgentCore). Long agent runs also hit serverless function time limits.
- Listed only as the "everything is on fire" fallback; it would need a spec change.

### Repo layout options
- **Monorepo (recommended):** layout from CLAUDE.md, plus `packages/schema/` for the shared plan schema. One PR can change agent + schema + web together, which keeps PRs coherent.
- **Two repos (agent, web):** cleaner deploys, but cross-repo PRs and duplicated CI. Not worth it for 4 days.

## Decision

**Accepted: Option A + monorepo.** Python 3.12 + Strands Agents SDK for `agent/` and `evals/`, Next.js + TypeScript for `apps/web/`, JSON Schema in `packages/schema/` as the single source of truth for the plan contract.

Proposed tooling (each is a new dependency, needs Adam's approval):
| Area | Python | TypeScript |
|---|---|---|
| Package mgr | `uv` | `pnpm` |
| Format/lint | `ruff` (format + lint) | `prettier` + `eslint` |
| Types | `mypy --strict` on `agent/` | `tsc --noEmit` |
| Tests | `pytest` | `vitest` (only if web has logic worth testing) |
| Pre-commit | `pre-commit` framework (runs both + `gitleaks`) | |

Model: pin one Claude model ID on Bedrock in config (`agent/config.py`), not in code. Prefer a Sonnet-tier model for latency (< 45s plan, < 15s replan); revisit only if eval scores require it. The model ID is logged with every trace and stored in `trips.model`.

Final layout:
```
apps/web/            Next.js + TS
agent/               Strands agent, tools/, config, AgentCore entrypoint
  tools/             get_route, find_stops_along_route, find_hotels_near, calculate_trip_cost, validate_plan
packages/schema/     plan.schema.json (+ generated TS types)
db/migrations/       numbered SQL migrations
db/seed/             CSV + loader
evals/               cases/, checks/, runner, results/
prompts/             planner.v1.md, ...
docs/                spec.md, adr/, plans/, review-checklist.md
.github/workflows/
```

## Consequences

- Two toolchains in CI and pre-commit. Day 1 PR 2 must set up both; budget ~1.5h.
- The tools in `agent/tools/` are plain Python functions with no Strands imports, so they can be unit-tested and reused by `validate_plan` and evals without starting an agent.
- Schema changes touch `packages/schema/` and require regenerating TS types (a CI check fails if generated types are stale).
- If AgentCore fails the 2h spike, the agent runs as a local process / Lambda with the same Strands code (spec cut-line).
