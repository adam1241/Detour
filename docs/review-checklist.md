# AI code review checklist (Detour)

You are reviewing a pull request for Detour. Read `CLAUDE.md` and `docs/spec.md` first. Your review is **advisory**: Adam makes the merge decision. Linters, formatters and type-checkers already run in CI, so don't comment on style they cover.

## How to report

- Post one review comment. Start with a one-line verdict: `No blocking issues` or `N blocking issues`.
- Then list findings, most severe first. For each: **[BLOCKING | SHOULD-FIX | NIT]**, `path:line`, what is wrong, a concrete failure scenario, and a suggested fix.
- **BLOCKING** = breaks a hard rule below, a spec requirement, or is a likely bug. Be sure before you use it.
- Cap NITs at 3. Skip praise. If you're unsure, say so instead of guessing.
- Don't propose large refactors that the PR didn't ask for.

## 1. Scope and process
- [ ] PR does one thing and matches its branch prefix (`feat/`, `fix/`, `chore/`, `docs/`).
- [ ] Under ~400 changed lines (seed data and generated files excepted, and say so).
- [ ] Description has: What, Why, How tested, Eval delta (if agent/prompt/schema changed), Risks.
- [ ] Nothing in the diff contradicts `docs/spec.md` or an accepted ADR. If behaviour changed, spec/README/ADR are updated in the same PR.
- [ ] Out-of-scope work from the spec's non-goals (bookings, weather, nationwide, H3 code, mobile) is flagged.

## 2. Items that require Adam's explicit approval (flag, don't judge)
Flag clearly if the PR does any of these:
- [ ] Adds or upgrades a dependency (`pyproject.toml`, `package.json`, lockfiles).
- [ ] Changes DB schema, or edits a migration that already exists on `main`.
- [ ] Changes `.github/workflows/`, branch protection, eval thresholds or tolerance.
- [ ] Touches secrets, `.env*`, IAM, or cloud resources that cost money.
- [ ] Deletes files or tests.

## 3. Hard rules (any violation is BLOCKING)
- [ ] **No invented data.** No hotel, stop, price, distance or duration is hard-coded in agent code or prompts. They come from tool results only.
- [ ] Every plan path runs `validate_plan` **and** JSON schema validation before returning.
- [ ] No test or eval is disabled, skipped, loosened, or has its threshold lowered to make CI pass.
- [ ] No secrets, tokens or keys in code, config, fixtures, logs or test output.
- [ ] Seed rows are tagged `source = 'sample_public'`. No logos or trademarks used as branding; "unofficial concept" labelling intact.
- [ ] Prompts changed only in `prompts/` with a new version in the filename, and `prompt_version` updated where it is logged.

## 4. Tests
- [ ] Deterministic logic (spatial queries, cost math, validation) has tests, ideally added before or with the code.
- [ ] Tests assert behaviour, not implementation details; boundary cases covered (exactly at limit, just over, empty input).
- [ ] No network calls in CI tests (routing uses fixtures).
- [ ] New eval cases have a clear expected outcome; bad plans found in testing were added as cases (spec §9.8).

## 5. Spatial correctness
- [ ] Coordinate order is **lon, lat** in GeoJSON/PostGIS; any lat/lon conversion is explicit.
- [ ] SRID 4326 everywhere; no mixing SRIDs.
- [ ] Distances in metres use **geography** (`::geography`); `ST_DWithin` on geometry 4326 would be in degrees (bug).
- [ ] `ST_LineLocatePoint` / `ST_LineSubstring` used on geometry, not geography.
- [ ] Queries can use the GIST / geography expression index (no function wrapping that defeats it).
- [ ] Units match the contract: `distance_km`, `duration_min`, `drive_minutes`, `max_detour_km`.

## 6. Money, points and plan rules
- [ ] Money is integer cents or `Decimal`, never float arithmetic; rounding happens once, at display.
- [ ] N days = N−1 overnights; the last day (and only the last day) has `overnight: null`.
- [ ] Budget is **lodging only** (hotel `price_usd` + host `est_cost_usd`); no gas, food or membership costs. Cash total = sum of the per-night breakdown.
- [ ] Points = points to **pay for hotel nights**; host nights add 0 points; nothing computes points earned.
- [ ] `hotel_nights + host_nights` = number of overnights.
- [ ] Hard rules from spec §5.1 enforced in `validate_plan`: max drive/day, budget, overnights from tool results, vehicle rule (`car` → hotels only for overnights), route order (no backtracking), one-sentence reason per overnight, conflicts surfaced in `warnings` with at least one `alternatives` entry.
- [ ] Replans change only the target day and later days; earlier days are identical.
- [ ] Overnight reasons use drive time, consecutive host nights or budget only (no weather).
- [ ] Soft rules are suggestions/warnings, not hard failures.

## 7. Agent, tools and LLM safety
- [ ] Tool input/output matches spec §5.3 contracts and the JSON Schema in `packages/schema/`.
- [ ] Tool errors return structured errors the agent can act on; no bare exceptions leaking stack traces to users.
- [ ] User text is never interpolated into SQL (parameterised queries only) and is length-limited.
- [ ] User text can't override system rules (e.g. "ignore the budget"); tool results are treated as data.
- [ ] Each run records model, prompt_version, tool calls, latency, tokens, cost (spec §7).
- [ ] Anything that could break the latency targets (first progress < 3s, plan < 45s, replan < 15s), such as extra model calls or N+1 queries, is called out.
- [ ] No routing API calls at runtime; `get_route` serves from `db/seed/corridor_route.geojson`.

## 8. Web
- [ ] UI renders only schema-validated plans; invalid plan shows a friendly error, not a crash.
- [ ] No server secrets in client bundles (`NEXT_PUBLIC_*` checked).
- [ ] Map attribution for tiles/OSM data is shown.
- [ ] Footer shows: unofficial concept, sample data, "example host-style stops" (never claims a business is a Harvest Hosts member).
- [ ] Totals say budget is lodging only and points are points to pay for hotel nights.
- [ ] Plan is hydrated with coordinates on the server only **after** validation.
- [ ] Plan endpoint keeps the 10 plans per IP per hour rate limit.

## 9. Migrations and data
- [ ] New migration file; existing migrations untouched.
- [ ] Migration is idempotent or guarded; has GIST indexes for new `geom` columns.
- [ ] Seed loader is idempotent (safe to re-run).
- [ ] Every seed row has a `source_url` to a page that was actually fetched; no row without a source. Flag rows whose values don't look like they came from that page.
- [ ] No text calls a place a Harvest Hosts member.
