# ADR-005: Web app hosting, map library and web↔agent API

- **Status:** Accepted by Adam, 2026-11-17
- **Spec refs:** §2 goal 1, §5.2, §7, §8.2 (latency), §9.9 (cost controls), §10 Day 3

## Context

The web app renders a route map and day cards from validated plan JSON and must be usable by a stranger on a live URL. Targets (spec v0.2): first progress message < 3s, full plan < 45s, replan < 15s. Open points:
- How the browser reaches the agent without exposing AWS credentials.
- How to keep a 20–45s agent run from feeling broken.
- Which map library/tiles: some need tokens, some have strict tile-usage policies.
- The plan schema (§5.2) has IDs only, no coordinates or geometry, so the map can't draw anything from the plan alone.

## Options

### Agent invocation
| | Next.js route handler → AgentCore (SigV4, server-side IAM creds) | Browser → AgentCore with JWT (Cognito/OIDC inbound auth) | Next.js → local/Lambda agent over HTTP |
|---|---|---|---|
| Secrets in browser | None | Short-lived token | None |
| Setup | IAM creds in Vercel env | Identity provider setup | Simple |
| Timeouts | Bound by serverless function limits | No proxy limit | Same as first |

### Progress UX
- **Stream** status events over SSE ("finding route… checking 12 wineries… validating plan").
- **Poll** a `trips` row for status.

### Map
| | MapLibre GL + free vector tiles | Mapbox GL JS | Leaflet + OSM raster tiles |
|---|---|---|---|
| Token | None / free key | Token | None |
| Policy | Check tile provider terms | Free tier, then paid | OSM tile policy discourages app traffic |
| React | `react-map-gl` (MapLibre) | `react-map-gl` | `react-leaflet` |

### Hosting
- **Vercel** (spec): previews per PR out of the box.
- AWS Amplify: same cloud as AgentCore, weaker preview story.

## Decision

- **Vercel Hobby (free) tier** for `apps/web`, preview per PR. Hobby is for non-commercial use, which fits a personal proof of concept.
- **Next.js route handler → AgentCore via SigV4.** Credentials in Vercel env vars for a least-privilege IAM user (only `InvokeAgentRuntime` on one runtime). An env flag points the same handler at the local/Lambda fallback.
- **Streamed progress (SSE):** the first event is sent before any DB or model call, so the < 3s first-progress target doesn't depend on Neon's cold start or the model.
- **MapLibre GL via `react-map-gl`** + a free vector tile provider, with map and OSM attribution shown.
- **Hydration after validation:** the route handler returns `{ plan, places, route_geojson }`. `places` maps each ID to name, lat, lon, price and points, read from the DB on the server **after** `validate_plan` and schema checks pass. The plan itself stays IDs-only.
- **Rate limit:** 10 plans per IP per hour on the plan endpoint (replans count as plans).
- **UI disclosures:** footer says unofficial concept, sample data, and that stops are example host-style stops, not claimed Harvest Hosts members. Totals say budget is lodging only; points are points to pay for hotel nights.

## Consequences

- New dependencies (`react-map-gl`, `maplibre-gl`, AWS SDK for JS) → approval on Day 3.
- Secrets (AWS creds, tile key if needed) are set up by Adam; IAM scope documented in the README.
- The response contract (`plan + places + route_geojson`) gets a small JSON Schema next to the plan schema in `packages/schema/`.
- **Risk:** a 45s run may exceed the Hobby tier's function duration limit. Check the current limit on Day 3. If it's too short, switch to browser → AgentCore with a short-lived JWT (second option above) via a follow-up ADR.
- In-memory rate limiting resets per serverless instance. Acceptable for a demo; the README says so. A shared store (e.g. Upstash) would be a new dependency.
