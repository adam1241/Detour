# Detour

An AI road-trip planner that mixes hotel nights with outdoor host-style stops (wineries, farms, breweries, museums) along one corridor: Washington, DC → Shenandoah / Skyline Drive → Blue Ridge Parkway → Asheville, NC.

> **Unofficial proof of concept.** Not affiliated with or endorsed by Choice Hotels International or Harvest Hosts. No logos or trademarks are used as branding.
>
> **Sample data.** All hotels and stops are a hand-curated sample from public pages, with a source URL on every row. Prices and points are sample values, not live rates.
>
> **Example host-style stops.** Places are shown as examples of the kind of stop a host network offers. We never claim that any business is a Harvest Hosts member.
>
> **Road conditions are not reflected.** Closures (including Hurricane Helene-related Blue Ridge Parkway closures) and seasonal closures are not modelled. The route uses towns as waypoints (Front Royal, Luray, Staunton, Roanoke, Boone, Asheville), not Parkway segments, so drive times follow town-to-town roads.

**Status:** Day 1 of 4 (kickoff). Nothing runnable yet.

## Why

In 2026 Choice Hotels agreed to acquire Harvest Hosts, an RV network of 11,200+ host locations [S1], which isn't yet part of Choice Privileges [S2]. Choice's 2026 AI launches mostly serve hotel owners [S3], and commentators have asked who is building AI for the guest [S4]. Detour tests a bet: a planner that adds hotel nights where they make sense turns outdoor travelers into hotel guests, while car travelers discover host-style places as daytime stops and still sleep at hotels.

Sources: [`docs/context-sources.md`](docs/context-sources.md).

## Docs

- [Spec (v0.3)](docs/spec.md): source of truth
- [ADRs](docs/adr/): 001 agent stack · 002 database and routing · 003 spatial search · 004 evals, merge gate, tracing · 005 web app, map, web↔agent API
- [Plans](docs/plans/)
- [AI review checklist](docs/review-checklist.md)
- [CLAUDE.md](CLAUDE.md): rules for AI-assisted work in this repo

## How this was built

_Filled in on Day 4: pipeline diagram, example PRs (including human overrides of AI suggestions), eval history and an AI-usage note._

**Merge process, stated openly:** branch protection requires pull requests and passing status checks (CI, plus evals when the agent, prompts or evals change), but **not** an approving review. GitHub doesn't let a PR author approve their own PR, and Adam is the only human on the project. Adam is the only one who merges and leaves a written review comment on every PR. The AI code review (OpenAI Codex) is advisory. See [ADR-004](docs/adr/004-evals-merge-gate-and-tracing.md).
