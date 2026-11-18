# ADR-004: Eval harness, merge gate and run tracing

- **Status:** Accepted by Adam, 2026-11-17; amended 2026-11-18 (AI reviewer vendor)
- **Spec refs:** §2 goal 6, §8.1, §9.5–9.9, §10 Day 2 and Day 4

## Context

Spec §8.1 gates merges on eval scores and on comparison with `main`. Problems to solve:
- **Non-determinism.** LLM output varies run to run. With 20 cases, one flaky case moves a metric by 5 points (95% → 90%), exactly at the threshold. A strict "not below `main`" rule would block good PRs at random.
- **Cost and secrets.** Every eval run calls Claude on Bedrock: it costs money and needs AWS credentials in CI.
- **Which PRs run evals?** A README typo should not spend money.
- **Solo approval.** GitHub does not let a PR author approve their own PR. With "required approvals: 1", Adam (the only human) could never merge.
- **Tracing.** Spec §7 wants tool calls, order, latency, tokens, cost and prompt_version for every run. Evals and production should share the trace format so a bad production plan can become an eval case.

## Options

### Eval runner
| | Custom `pytest` runner | promptfoo | Braintrust / LangSmith |
|---|---|---|---|
| Deps | None new beyond pytest | Node tool + config | SaaS account + SDK + key |
| Fit with deterministic checks | Direct: checks reuse `validate_plan` | Via custom asserts | Via scorers |
| Results history | JSON files in `evals/results/` | Local/DB | Hosted UI |
| Risk | We write the reporting | Learning curve | Another account and secret |

### Handling non-determinism
1. Temperature 0 + deterministic tools (ADR-002 cached corridor).
2. Run each case k=3 times, majority vote. Triples cost.
3. Compare to `main` with a tolerance of one case.

### Merge approval
1. Required approving review from a second GitHub account. Feels like theatre.
2. Required status checks only; Adam is the only person who merges and leaves a written review on every PR.

### Tracing
| | AgentCore Observability (CloudWatch, OpenTelemetry) | Langfuse | Own `trips.trace_json` |
|---|---|---|---|
| Setup | Built into AgentCore; Strands emits OTel spans | SDK + key | Column in `trips` |
| Works in local fallback | Partially | Yes | Yes |
| Reviewer can see it | Needs AWS console | Shareable UI | Via README queries/screenshots |

## Decision

- **Runner:** custom `pytest` runner in `evals/`. Each case is a YAML file with `request`, persona, `vehicle`, `outdoor_leaning: bool`, `infeasible: bool`, and expected constraints. Replan cases include the original plan and the follow-up. Checks are Python functions; grounding and route-order checks call the same code as `validate_plan`. Results go to `evals/results/<git-sha>.json` and a summary PR comment.
- **Checks gated (spec §8.1):** grounding 100%, schema 100%, max drive ≥ 95%, budget ≥ 95%, route order ≥ 95%, vehicle rule ≥ 95%, replan-earlier-days-identical ≥ 95%.
- **Infeasible cases:** a broken constraint passes **only if** the case is labeled `infeasible: true` **and** the plan lists the violation in `warnings` **and** has at least one entry in `alternatives`. Otherwise it fails.
- **Variance:** temperature 0 + deterministic tools; k=1. A PR fails if a gated score is below its threshold, or if a ≥ 95% check is **more than 1 case worse** than `main`'s last recorded result. **Zero tolerance** on grounding and schema.
- **Trigger:** evals run in CI only on PRs touching `agent/`, `prompts/` or `evals/`, plus manual `workflow_dispatch`. `main`'s baseline is refreshed on every merge that ran evals.
- **Merge approval (stated openly here and in the README):** branch protection requires PRs and status checks (`ci`, plus `evals` when triggered), blocks force-push, and **does not require approving reviews**, because GitHub doesn't let a PR author approve their own PR and Adam is the only human on the project. **Adam is the only one who merges** and leaves a written review comment on every PR. The AI review is advisory.
- **Tracing:** Strands/OTel spans → AgentCore Observability when on AgentCore, plus one `trips` row per run with `trace_json` (tool calls in order, per-call latency, tokens, cost), `model` and `prompt_version`. The `trips` row works in the local fallback too. Langfuse only if time remains.
- **Cost controls:** AWS budget alarm at $20; spend limit on the AI reviewer's OpenAI API key. Adam sets up all secrets (prefer a GitHub OIDC role for Bedrock over static keys).

### Amendment 2026-11-18: AI code reviewer

**Context.** The advisory AI review (spec §9.6.2) was first built on `anthropics/claude-code-action`. Adam chose OpenAI for the reviewer and set `OPENAI_API_KEY`.

**Options.** (a) Keep Claude: one vendor, same model family that writes the code. (b) OpenAI Codex via the official `openai/codex-action`: a second vendor reviewing Claude-written code (more independent), Adam's preference on cost/quality.

**Decision.** (b). `openai/codex-action@v1`, model left to the action's default (pin later if reviews vary). The planner agent stays Claude on Bedrock; this only changes the reviewer.

**Security design** (`.github/workflows/ai-review.yml`):
- Job `review` (read-only token): a trusted step fetches the diff; Codex runs as the **last step** with `safety-strategy: drop-sudo` (the action's guidance for keeping the API key out of reach) and `sandbox: read-only` (no writes, no network). Codex cannot post.
- Job `post` (fresh runner, write token): scans the answer for credential-like strings, then posts or updates one comment marked `<!-- detour-ai-review -->`.
- Only same-repository, non-draft PRs are reviewed.

## Consequences

- Changing thresholds, tolerance or the eval trigger paths needs Adam's approval (CLAUDE.md).
- Eval cost per run is measured on Day 2 and added here.
- Without required approvals, the "human approval" guarantee rests on process (only Adam merges, written review on every PR), not on GitHub enforcement. The README says this plainly.
