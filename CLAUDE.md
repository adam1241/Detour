# CLAUDE.md: Rules for working in this repo

Read `docs/spec.md` before any task. It is the source of truth. If a request conflicts with the spec, stop and ask.

## Project in one line
Detour is an AI road-trip planner that mixes hotel nights with outdoor host stops (wineries, farms) along a route. It's an unofficial proof of concept with sample data.

## How we work (non-negotiable)
1. **Plan before code.** For any feature, propose a plan first (files to touch, approach, tests, risks). Wait for approval before implementing.
2. **Never commit to `main`.** One feature per branch (`feat/…`, `fix/…`, `chore/…`), small PRs (aim for under ~400 changed lines).
3. **Tests first** for deterministic logic: spatial queries, cost math, plan validation. Write the failing test, then the code.
4. **Run checks before saying "done":** format, lint, type-check, tests. Report the actual output and don't claim success you didn't verify.
5. **Decisions go in ADRs.** Any choice of library, provider, schema or architecture gets a short ADR in `docs/adr/NNN-title.md` (Context, Options, Decision, Consequences). Propose it; Adam decides.
6. **Update docs in the same PR** when behavior changes (spec, README, ADRs).
7. **Write PR descriptions** with: What, Why, How tested, Eval delta (if agent or prompt changed), Risks.

## Hard rules for the agent and data
- The planner must **never invent** hotels, stops, prices or distances. All of these come from tool results only.
- Every plan must pass `validate_plan` and the JSON schema before it's returned.
- All seed data is tagged `source = 'sample_public'`. Don't add Choice logos or trademarks as branding.
- System prompts live in `prompts/` with a version in the filename. Changing a prompt means a PR and an eval run.

## Requires Adam's explicit approval
- Adding a dependency
- Changing the DB schema or editing an applied migration
- Changing CI workflows, branch protection or eval thresholds
- Anything touching secrets, `.env` or cloud resources that cost money
- Deleting files or tests

## Never
- Commit secrets or API keys
- Disable, skip or weaken a test or eval to make it pass
- Make large unrequested refactors

## Time pressure
The whole project has 4 days (spec §10). Prefer the simplest thing that meets the acceptance criteria. If a task is running long, say so early and propose the cut-line from the spec, rather than pushing on silently.

## Repo layout (target, to be confirmed in ADR-001)
```
apps/web/        Next.js + TypeScript frontend
agent/           Agent code and tools (AgentCore)
db/              migrations/ and seed/
evals/           cases/, checks, runner, results history
prompts/         versioned system prompts
docs/            spec.md, adr/, plans/, review-checklist.md
.github/         workflows/ (CI, AI review, evals, preview)
```

## Commands
Python 3.12 via `uv` (ADR-001). TypeScript tooling arrives with the web app on Day 3.

| Task | Command |
|---|---|
| Install | `uv sync` |
| Install git hooks | `uv run pre-commit install` |
| All pre-commit checks | `uv run pre-commit run --all-files` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type-check | `uv run mypy` |
| Unit tests | `uv run pytest -m "not db"` |
| Apply migrations | `DATABASE_URL=postgresql://... uv run python -m db.migrate` |
| Load seed data | `DATABASE_URL=postgresql://... uv run python -m db.seed.load_seed` |
| Rebuild cached corridor route (network, run rarely) | `uv run python scripts/build_corridor.py` |
| Live agent config | `DETOUR_MODEL_ID=<bedrock id> AWS_PROFILE=detour AWS_REGION=us-east-1` (ADR-007) |
| Demo server (sample, no AWS) | `uv run python -m agent.server --sample` then open http://127.0.0.1:8000 |
| Rebuild demo sample plan | `uv run python -m scripts.build_sample_plan` |
| Run evals | `uv run python -m evals.runner --planner module:function [--seed-catalog]` (writes `evals/results/<sha>.json` + `.md`; exit 1 if the gate fails) |
| Spatial/DB tests | `DATABASE_URL=postgresql://... uv run pytest -m db` (needs PostGIS; CI uses `postgis/postgis:16-3.4`) |

Claude Code hooks (`.claude/settings.json`) block edits to `.env*` and to migrations already on `main`, and run unit tests after edits under `agent/`.

_Still to add: dev._

## Definition of done
- Acceptance criteria from the spec are met
- All checks pass locally and in CI
- Evals pass the thresholds in `docs/spec.md` §8 (if the agent was touched)
- Docs updated
- PR opened with a complete description; Adam approves the merge
