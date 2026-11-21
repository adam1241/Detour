# Detour: Project Spec (v0.3)

> **Working name:** Detour, a road-trip planner that mixes hotel nights with outdoor stops
> **Owner:** Adam
> **Status:** Day 4 in progress (v0.4)
> **Timebox:** 4 days
> **Note:** Unofficial proof of concept. Not affiliated with or endorsed by Choice Hotels International or Harvest Hosts. All hotel and host data in this repo is a hand-curated *sample* from public information, with a source URL on every row. Host places are **example host-style stops**: we never claim that a business is a Harvest Hosts member. No logos or trademarks are used as branding.

### Changelog
- **v0.7 (2026-11-21):** ADR-008 accepted: coverage will grow from the one corridor to the contiguous US (OpenStreetMap and RIDB places, OpenRouteService routing, nullable prices shown as "price not available"). Until the PRs in [docs/plans/nationwide.md](plans/nationwide.md) land, the product still covers the corridor only.
- **v0.6 (2026-11-21):** Prompt `planner.v2` (keep the requested trip, spread driving before breaking a limit, never ask questions). The loop sends back once a plan that passes only because a warning excuses a broken limit, with each day's drive time and a per-day target when the trip fits on average; a text reply after that challenge confirms the plan. "Cheaper" replans are soft requests (no hard budget). First live baseline in `evals/results/main.json`.
- **v0.5 (2026-11-20):** Planner model switched to OpenAI GPT-6 Luna (`gpt-6-luna`, Responses API with function calling) because the AWS account is blocked from Anthropic models on Bedrock. The Bedrock client stays and can be selected with `DETOUR_PROVIDER=bedrock` (ADR-007 amendment).
- **v0.4 (2026-11-20):** Agent loop on the Bedrock Converse API instead of Strands (ADR-007). Drive times, distances and totals in a plan are filled in by code from the tools; the model drafts the structure. Final-day cut-line: the agent runs locally (no AgentCore), and the demo is a local page instead of a Vercel deploy.
- **v0.3 (2026-11-18):** AI code reviewer switched from Claude to OpenAI Codex (`openai/codex-action`, pinned SHA, `pull_request_target` with trusted base policy), key in `OPENAI_API_KEY` in the `ai-review` environment (ADR-004 amendment). The planner agent stays Claude on Bedrock.
- **v0.2 (2026-11-17):** Applied kickoff answers. Added `vehicle` input (rv/car); defined days vs nights, budget (lodging only) and points (redemption); removed weather from reasons and the feedback table; fixed corridor waypoints and cached corridor route; `max_detour_km` default 30; new latency targets (first progress < 3s, plan < 45s); replan scope ("affected" days); infeasible-case eval rule and main-comparison tolerance; merge process without required approvals; cost controls; ADR-004 and ADR-005 added; business context sources added; Day 1 capped at 5 PRs.
- **v0.1:** Kickoff draft.

---

## 1. Why this exists

### 1.1 Business context
Sources are listed in [`docs/context-sources.md`](context-sources.md) (verified by Adam, 2026-11-17).
- Choice Hotels is an asset-light franchisor (~7,600 hotels, 22 brands, mostly economy-to-midscale, highway-adjacent). Its core customer is the value-minded road traveler.
- In Sept–Oct 2026 Choice agreed to acquire **Harvest Hosts** (~$130M), an RV membership network of 11,200+ host locations (wineries, farms, breweries, museums). Choice says its loyalty members over-index among RV travelers. [S1] Harvest Hosts is not yet part of Choice Privileges. [S2]
- **Open question in the industry:** will RV/outdoor travelers ever become *hotel* guests? Franchisees want to know what the acquisition does for them.
- Choice's 2026 AI launches (CHARLIE, RAISE, EasyBid, and others) mostly serve **hotel owners**. [S3] A guest-facing AI that creates travel demand is a visible gap. [S4]

### 1.2 The problem we solve
Road-trippers plan across many tabs (maps, hotel sites, RV apps, blogs). No single tool builds a trip that **mixes** unique outdoor stops with hotel nights and explains the trade-offs (cost, comfort, drive time).

### 1.3 The bet
A planner that naturally inserts hotel nights where they make sense (long drive days, need for a real bed and shower, budget room) turns outdoor travelers into hotel guests. Car travelers discover host-style places as daytime stops and still sleep at hotels. It also gets people excited to take a trip in the first place.

---

## 2. Goals and non-goals

### Goals (v1)
1. A user describes a trip in plain language and gets a **day-by-day plan** on a map. First progress message in **< 3s**, full plan in **< 45s**, with progress streamed to the page.
2. Every hotel and stop in a plan is **real data returned by a tool**: zero invented places.
3. Plans respect hard constraints: max driving per day, total lodging budget, and vehicle type.
4. Users can **replan** with follow-ups ("make it cheaper", "more outdoors", "make day 2 shorter") without starting over.
5. Show lodging totals in **cash and points** (points = points needed to pay for hotel nights).
6. Ship with an **eval suite** that gates merges, plus basic production tracing.
7. Demonstrate a disciplined **AI-assisted engineering workflow** (see §9).

### Non-goals (v1)
- Real bookings, payments, accounts or live availability.
- Real Choice or Harvest Hosts APIs (we use sample data with the same *shape*).
- Nationwide coverage. v1 covers **one corridor** (see §6).
- Weather-aware planning.
- Live road conditions or closures (including Helene-related Blue Ridge Parkway closures).
- Points earned (only points to redeem are shown).
- Gas, food or membership costs in the budget.
- Feedback button and feedback table, H3 indexing, LLM-as-judge gating (stretch only, if time remains).
- Mobile app.

---

## 3. Users

| Persona | Vehicle | Description | What they want |
|---|---|---|---|
| **The RV couple** | `rv` | Retired or semi-retired, already RV-membership travelers | Unique host overnights, low cost, a hotel night every few days for laundry and a real shower |
| **The car road-tripper family** | `car` | 2 adults + kids, budget-conscious | Short drive days, kid-friendly daytime stops, predictable hotels |
| **The weekend explorer** | `car` | Young professional in DC | A 2–3 day escape with a winery or farm stop, low effort to plan |

---

## 4. User stories

- **US-1:** As a traveler, I type "DC to Asheville, 4 days, under $600, by car, we like wineries" and get a plan with a route map, one card per day, and totals. If I don't say a vehicle, the planner assumes `car` and says so.
- **US-2:** As a traveler, I can say "make day 2 shorter" and only the affected days change: day 2 and every later day. Earlier days stay identical.
- **US-3:** As a traveler, I can see why each overnight was chosen, based on drive time, consecutive host nights and budget ("hotel tonight: 5h 40m of driving today" / "hotel tonight: two host nights in a row, time for a real shower" / "farm stay: saves $95").
- **US-4:** As a traveler, I can see the lodging cost in dollars and the points needed to pay for the hotel nights, side by side.
- **US-5:** As a reviewer (Choice), I can read how often the planner inserts hotel nights into outdoor trips (the "hotel conversion rate" of plans).

---

## 5. Functional requirements

### 5.0 Definitions
- **Trip length:** "N days" = N travel days and **N−1 overnights**. The last day ends at the destination with `overnight: null`.
- **Budget:** lodging only = sum of hotel `price_usd` + host `est_cost_usd` for the overnights. Excludes gas, food and memberships. The UI says so.
- **Points:** points needed to pay for hotel nights (redemption), shown next to the cash price. Host nights = 0 points. Points earned is out of scope.
- **Vehicle:** `rv` or `car` (default `car` if not stated, with a warning). `rv`: host stops can be overnights. `car`: host places are daytime stops only and every overnight is a hotel.
- **Affected days (replan):** the target day and every later day. Earlier days must stay identical.

### 5.1 Planning rules (agent must follow)
| Rule | Type | Default |
|---|---|---|
| Max driving per day | Hard | 5h (user can change) |
| Total lodging budget | Hard, if given | n/a |
| Every overnight is a hotel or a host stop from tool results | Hard | n/a |
| Host overnights only when `vehicle = rv`; `car` overnights are hotels | Hard | n/a |
| Overnights appear in route order (no backtracking) | Hard | n/a |
| N days → N−1 overnights; last day `overnight: null` | Hard | n/a |
| Replan changes only the target day and later days | Hard | n/a |
| Max consecutive host-stop nights before suggesting a hotel | Soft | 2 |
| Prefer a hotel after any drive day over 4h | Soft | on |
| Explain each overnight choice in one sentence (drive time, consecutive host nights, budget) | Hard | n/a |
| When constraints conflict, say so in `warnings` and offer at least one option in `alternatives`; never silently break a hard rule | Hard | n/a |

### 5.2 Plan output schema (JSON, validated before display)
```json
{
  "trip_id": "string",
  "summary": "string",
  "origin": "string",
  "destination": "string",
  "vehicle": "rv | car",
  "days": [
    {
      "day": 1,
      "drive_minutes": 0,
      "distance_km": 0,
      "stops": [{ "id": "stop_123", "type": "winery", "reason": "string" }],
      "overnight": { "kind": "hotel | host", "id": "hotel_456", "reason_code": "drive_time | consecutive_host_nights | budget", "reason": "string, one sentence" },
      "narrative": "string, 2-3 sentences, inspiring"
    }
  ],
  "totals": { "cash_usd": 0, "points": 0, "hotel_nights": 0, "host_nights": 0 },
  "warnings": ["string"],
  "alternatives": ["string"]
}
```
- `overnight` is `null` on the last day and only there.
- `totals.cash_usd` is lodging only; `totals.points` covers hotel nights only.
- The plan holds IDs only. After validation, the server **hydrates** it for display with `places` (id → name, lat, lon, price, points) and `route_geojson` (ADR-005).

### 5.3 Agent tools (contracts)
| Tool | Input | Output |
|---|---|---|
| `get_route` | origin, destination, optional waypoints | GeoJSON LineString, distance_km, duration_min, legs[] |
| `find_stops_along_route` | route geometry, types[], max_detour_km (default **30**), limit | stops[] with `distance_from_route_km` and `position_along_route` (0–1) |
| `find_hotels_near` | point or route segment, max_price_usd, radius_km | hotels[] with price and points_per_night |
| `calculate_trip_cost` | draft plan | cash total (lodging), points total (hotel nights), per-night breakdown |
| `validate_plan` | draft plan | list of rule violations (empty = valid) |

The agent must call `validate_plan` before returning a plan, and fix any violations it finds.

Implementation (PR "plan validation", 2026-11-19):
- Schema: [`packages/schema/plan.schema.json`](../packages/schema/plan.schema.json) (JSON Schema 2020-12). Each overnight carries a `reason_code` (`drive_time`, `consecutive_host_nights`, `budget`) plus a one-sentence `reason`.
- `calculate_trip_cost` (`agent/plan/cost.py`): lodging cash (hotel price or host fee), points for hotel nights only, per-night breakdown; money as `Decimal`. Overnights whose kind doesn't match their place are not priced.
- `validate_plan` (`agent/plan/validate.py`) returns violations with a stable `code`: `schema`, `day_numbers`, `overnight_missing`, `last_day_overnight`, `narrative_length`, `unknown_id`, `not_from_tools`, `kind_mismatch`, `type_mismatch`, `not_overnight_host`, `vehicle_rule`, `reason_format`, `route_error`, `drive_mismatch`, `distance_mismatch`, `max_drive`, `backtracking`, `budget`, `totals_mismatch`, `replan_changed_earlier_day`.
- Grounding: IDs must exist in the catalog and, when the agent passes `allowed_ids`, must have been returned by a tool in this run.
- Drive time and distance per day = `get_route` between the previous overnight and the next, plus side trips: off-route stops (there and back) and off-route lodgings, estimated as straight-line distance to the route x 1.3 at 50 km/h. Tolerance: 5 min or 5%, 2 km or 5%. The max-drive rule uses the same total.
- Route order: every stop and overnight must lie on the way from origin to destination (2% or 5 km slack), and its distance along the way must not go down (1 km tolerance).
- Cash totals must equal the cost tool exactly; non-finite numbers are schema errors.
- `max_drive` and `budget` are `acknowledged` (not blocking) only when the plan has an alternative and a warning about that exact violation (`max_drive`: a warning naming "Day N" and driving; `budget`: a warning mentioning the budget).

---

## 6. Data

### 6.1 Scope
**One corridor:** Washington, DC → Front Royal → Luray → Staunton → Roanoke → Boone → Asheville, NC.
- The waypoints are **towns, not Parkway segments**. Town-to-town routing roughly follows Skyline Drive and the Blue Ridge Parkway; with `max_detour_km = 30` the search covers places along both.
- The corridor route is fetched once with `get_route` (with the waypoints above) and cached as `db/seed/corridor_route.geojson`. No routing table.
- Sample data target: **Day 1: 15 hotels + 15 stops**; expand toward ~25 + ~25 on Day 2 if time allows.
- Sourcing rule: a row is drafted only from a page that was actually fetched, and every row has a `source_url`. Adam reviews every row before merge. No invented rows.
- RV overnight hosts (`overnight_ok = true`): the host's own website, **or its own Hipcamp listing**, must advertise RV stays and a nightly price (approved by Adam, 2026-11-18). Harvest Hosts listings are never used, and no stop is described as a Harvest Hosts member.
- All rows tagged `source = 'sample_public'`.

### 6.2 Data model (PostGIS)
```
hotels(id, name, brand_segment, geom POINT, price_usd?, points_per_night?, amenities[], source, source_url, source_ref?, licence?)
stops(id, name, type, geom POINT, overnight_ok BOOL, est_cost_usd?, description, source, source_url, source_ref?, licence?)
trips(id, request_text, vehicle, plan_json, trace_json, created_at, model, prompt_version, eval_tags[])
```
- Spatial indexes (GIST) on all `geom` columns, plus a GIST expression index on `(geom::geography)` (ADR-003).
- Constraints (migration `0001_init`): `brand_segment` in economy/midscale/upscale; stop `type` in winery/farm/brewery/museum; `vehicle` in rv/car; prices `numeric(8,2)`; `source_url` must start with `https://`.
- `source` is `sample_public` for real sample rows. `test_fixture` is allowed only for synthetic test rows, and the seed loader refuses it outside tests.
- Migration `0002_nationwide` (ADR-008): `source` also allows `osm` and `ridb` (imported rows). Prices (`?` above) are required on hand-sourced rows and forbidden on imported rows: prices only come from a fetched page. Imported rows need `source_ref` (`osm:node/123`, `ridb:facility/456`, unique) and `licence` (`ODbL-1.0` for OSM, `US-PD` for RIDB). The loader's bounding box is the contiguous US. Sourcing rules and price/points tiers: [`db/seed/README.md`](../db/seed/README.md).
- The corridor search uses `ST_DWithin` against the route LineString (geography type, meters).
- `trace_json` holds tool calls, order, latency, tokens and cost for the run (ADR-004).
- The feedback table is dropped for v1.

---

## 7. Architecture

```
[Next.js web app on Vercel] ──HTTP (SSE progress)──> [Next.js route handler]
      │                                                   │ SigV4
      │                                                   v
      │                              [Agent on AWS Bedrock AgentCore (OpenAI GPT-6 Luna, Python loop)]
      │                                                   │ tool calls
      │                                                   ├── get_route ──────────> cached corridor route (built with OSRM)
      │                                                   ├── find_* ─────────────> Neon Postgres + PostGIS
      │                                                   ├── calculate_trip_cost
      │                                                   └── validate_plan
      └── renders map + day cards from the validated, server-hydrated plan
Tracing: every run logs tool calls, order, latency, tokens, cost, prompt_version
```

**Decisions** (ADRs in `docs/adr/`, all Accepted 2026-11-17):
- [ADR-001](adr/001-agent-stack-and-repo-layout.md): Python agent (framework amended by ADR-007), TypeScript Next.js web, monorepo, JSON Schema in `packages/schema/` as the single plan contract.
- [ADR-002](adr/002-database-host-and-routing-provider.md): Neon Postgres + PostGIS; corridor route built once with the public OSRM server via fixed town waypoints and cached as a GeoJSON file.
- [ADR-003](adr/003-spatial-search.md): PostGIS corridor search for v1; H3 documented as the scale path.
- [ADR-004](adr/004-evals-merge-gate-and-tracing.md): custom pytest eval runner, merge gate with tolerance vs `main`, path-filtered eval runs, merge process without required approvals, tracing.
- [ADR-005](adr/005-web-app-map-and-agent-invocation.md): Vercel Hobby, server-side SigV4 calls to AgentCore, streamed progress, MapLibre, plan hydration, per-IP rate limit.
- [ADR-006](adr/006-database-migrations.md): plain numbered SQL migrations with a small tested runner (`db/migrate.py`); fix forward, no down-migrations.
- [ADR-007](adr/007-agent-loop-bedrock-converse.md): own tool-use loop (Converse-shaped), plan returned only through `validate_plan`, numbers filled from tools; amended: OpenAI GPT-6 Luna by default, Bedrock selectable.
- [ADR-008](adr/008-nationwide-coverage.md): nationwide coverage from OpenStreetMap and RIDB places, OpenRouteService routing, prices only where sourced; plan in [docs/plans/nationwide.md](plans/nationwide.md).

---

## 8. Quality: evals and success metrics

### 8.1 Eval suite (`/evals`)
- **20 trip requests** across personas, vehicles, budgets, lengths and edge cases (impossible budget, 1-day trip, "no hotels at all", vague request, replans).
- Each case is labeled with `vehicle`, `outdoor_leaning: bool` and `infeasible: bool`.
- **Automated checks per plan:**
  | Check | Threshold to merge |
  |---|---|
  | Grounding: every ID exists in the DB | **100%** |
  | Valid against JSON schema | **100%** |
  | Max drive-per-day respected | ≥ 95% |
  | Budget respected (when given) | ≥ 95% |
  | Route order (no backtracking) | ≥ 95% |
  | Vehicle rule (no host overnights for `car`) | ≥ 95% |
  | Replan: earlier days identical | ≥ 95% |
- **Infeasible cases:** a broken constraint passes **only if** the case is labeled `infeasible: true` **and** the plan lists the violation in `warnings` **and** offers at least one entry in `alternatives`. Otherwise it fails.
- **Comparison with `main`:** a PR fails if any gated score drops below its threshold, or if a ≥ 95% check is **more than 1 case worse** than `main`'s last recorded result. Grounding and schema have **zero tolerance**.
- **Tracked, not gated:** narrative quality (LLM-as-judge, 1–5, stretch), latency (first progress, full plan) p50/p95, cost per plan, hotel-conversion rate (share of `outdoor_leaning` trips that include ≥ 1 hotel night).
- **Implementation (`evals/`):** 20 cases in `evals/cases/*.yaml` (labels: persona, vehicle, `outdoor_leaning`, `infeasible`, optional `replan`); checks in `evals/checks.py` built on `validate_plan`; runner `python -m evals.runner`. The grounding check covers invented numbers too (`drive_mismatch`, `distance_mismatch`, `totals_mismatch`), not only unknown IDs, since CLAUDE.md forbids invented distances and prices. A planner crash or missing plan fails every applicable check. The `schema` gate also requires a well-formed day/overnight structure and a plan that answers the request (same origin, destination and day count as the case, when the case specifies them); the `vehicle` gate requires the plan's vehicle to match the case. Replans receive a copy of the base plan. Tracked latency is reported separately for first progress and full plan (p50/p95).
- **When evals run in CI:** only on PRs that touch `agent/`, `prompts/` or `evals/` (plus manual trigger).

### 8.2 Demo success criteria
- Live demo: first progress message in < 3s, a 4-day plan in < 45s, a replan in < 15s.
- Eval history shows improvement across versions.
- A reviewer can understand the system and the workflow from the README in 5 minutes.

---

## 9. Engineering workflow (AI-assisted, human-governed)

This project shows how to use AI to build fast **without** vibe coding.

1. **Spec first.** This doc, ADRs and a plan per feature come before code.
2. **Plan mode.** Claude Code proposes a plan for each feature. Adam reviews and edits it before implementation. Key plans are saved in `docs/plans/`.
3. **Small branches, small PRs.** One feature per branch. No direct commits to `main`.
4. **Tests first** for deterministic logic (spatial queries, cost math, validation).
5. **Local guardrails.** Pre-commit: format, lint, type-check, secret scan. Claude Code hooks run tests after edits and block edits to `.env` and applied migrations.
6. **PR pipeline (GitHub Actions):**
   1. Build, lint, type-check, unit tests, spatial tests on a PostGIS service container
   2. AI code review (OpenAI Codex, a different vendor from the planner model) against `docs/review-checklist.md` (advisory)
   3. Eval suite with a score comment and a regression gate (only when `agent/`, `prompts/` or `evals/` change)
   4. Preview deploy of the web app
   5. **Human merge.** Branch protection requires status checks, not approving reviews (GitHub does not let a PR author approve their own PR, and Adam is the only human). Adam is the only one who merges, and leaves a written review comment on every PR. This is stated openly in ADR-004 and the README.
7. **Prompts are code.** System prompts live in `prompts/` with versions, change through PRs, and must pass evals.
8. **Production.** Output schema validation at runtime and tracing on every run. Any bad plan found in testing becomes a new eval case.
9. **Cost controls.** AWS budget alarm at $20; spend limit on the AI review's OpenAI API key; Vercel Hobby (free) tier; public plan endpoint rate-limited to 10 plans per IP per hour. Adam sets up all secrets.
10. **Transparency.** The README has a "How this was built" section: pipeline diagram, example PRs (including human overrides of AI suggestions), eval history and an honest AI-usage note.

---

## 10. Milestones (4 days)

| Day | Deliverable | Done when | Cut-line if behind |
|---|---|---|---|
| **1: Foundation + data** | At most **5 PRs** (small setup steps merged together): kickoff docs; repo tooling + CI + pre-commit + hooks + AI review; DB schema + seed loader (5-row fixture, then 15 + 15 sourced rows); `get_route` + cached corridor; `find_stops_along_route` + `find_hotels_near` (tests first) | Spatial tests pass in CI; first PRs merged through the pipeline | AI review action slips to Day 2 morning; sourced rows land Day 2 if review isn't done |
| **2: The agent** | Agent on AgentCore with all tools, `validate_plan`, schema-valid output; prompt v1 in `prompts/`; **20 eval cases + deterministic checks written**; expand sample data toward 25 + 25 | One full plan returned end to end; eval runner works locally | AgentCore not working after 2h → run the same agent locally and document AgentCore as next step |
| **3: The product** | Web UI (map, route, day cards, narratives, streamed progress), replanning ("cheaper", "shorter days", "more outdoors"), cash + points totals; Vercel preview per PR; production deploy with rate limit | A stranger can plan and replan a trip on the live URL | Replanning limited to the three preset commands |
| **4: Proof + package** | Evals in CI as a merge gate (demo: a bad prompt PR gets blocked); tracing on; README "How this was built" + pipeline diagram; 2-min demo video; one-pager | Shareable package sent | Record the demo by noon; polish docs after |

## 11. Risks

| Risk | Mitigation |
|---|---|
| AgentCore setup takes longer than planned | Day 2 spike timeboxed to 2h; fallback: same agent run locally / on Lambda, with the AgentCore integration documented as next step |
| Agent invents places | Grounding check at runtime + 100% eval gate; tools are the only data source |
| Seed data invented or unsourced | Rows drafted only from fetched pages, `source_url` on every row, Adam reviews every row |
| Public routing API rate limits | Corridor route fetched once and cached in `db/seed/corridor_route.geojson`; no runtime routing calls |
| Road closures (incl. Helene-related Parkway closures) not reflected | Out of scope; README note; waypoints are towns, not Parkway segments |
| Implying a business is a Harvest Hosts member | Stops called "example host-style stops"; disclaimer in UI footer and README |
| 45s run exceeds serverless limits on Vercel Hobby | Stream progress; verify Hobby function duration limit on Day 3; fallback: call AgentCore from the browser via short-lived token (ADR-005) |
| Runaway cloud or API spend | $20 AWS budget alarm, AI review key spend limit, path-filtered evals, 10 plans/IP/hour |
| Scope creep | Non-goals list; one corridor only; daily cut-line (see §10) |
| Running out of time on Day 4 | Eval cases written on Day 2; demo recorded by Day 4 noon, polish after |
| Looks like an official Choice product | Clear "unofficial concept" labeling; no logos; sample data flagged |

---

## 12. Open questions
- ~~Which routing provider gives the best free tier for the demo?~~ Resolved: no runtime routing; corridor cached once via OSRM (ADR-002).
- ~~Should points values be per hotel (sample) or a flat conversion?~~ Resolved: per hotel, sample values, redemption only.
- Replan latency target stays < 15s while the full plan moved to < 45s. Confirm on Day 3 once real timings exist.
