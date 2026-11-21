# ADR-008: Nationwide coverage (data, routing, geocoding)

- **Status:** Proposed, 2026-11-21. Adam decides; nothing is built until this is accepted.
- **Spec refs:** §1 (one corridor), §5.1 (never invent places or prices), §5.3 tools, §6.1 sourcing rules, §8 evals
- **Plan:** [docs/plans/nationwide.md](../plans/nationwide.md)

## Context

v1 covers one corridor (DC to Asheville): a cached OSRM route, 15 hotels and 20 stops, each
priced by hand from its own public page. Adam wants plans **anywhere in the US** on a **real,
complete dataset**. Three facts shape the options:

1. **No open dataset has Choice hotel prices or points.** Rates live on choicehotels.com, one
   hotel and one night at a time. Collecting thousands of them means scraping the site, which
   we won't do (terms of use, and no human could review the rows as §6.1 requires).
2. **No open nationwide list of farms and wineries that host RVs, with fees, exists.** Harvest
   Hosts is off-limits (§6.1).
3. **Free routing and data services have usage limits** that fit a demo, not a public product.

## Options

### Places

| | A. OpenStreetMap, one-time import | B. Hand-sourced, per region | C. Scrape brand sites |
|---|---|---|---|
| What | Choice-brand hotels (by `brand:wikidata`), wineries, farms, breweries, museums from a US OSM extract (Geofabrik), loaded into PostGIS | Today's method: a few priced rows per region from each place's own page | Prices for every hotel |
| Coverage | Thousands of real places, nationwide | Tens of places per region | Complete |
| Prices | **None** (OSM has no rates) | Real, sampled | Real |
| Licence / rules | ODbL: attribution and share-alike for the derived database | Same as today | Breaks terms of use; unreviewable. **Rejected.** |
| Cost | One import script plus a filter tool (new dependency) | Hours per region | n/a |

**RV overnights:** the federal **Recreation Information Database (RIDB)** lists campgrounds on
public land from 12 agencies, is public domain, and has a free API key. Fees appear for some
facilities only. OSM `tourism=caravan_site` adds private RV parks, mostly without fees.

### Routing

| | A. OSRM public demo server | B. OpenRouteService (free key) | C. Self-hosted OSRM |
|---|---|---|---|
| Key | None | Free account | None |
| Limits | Best effort, can be withdrawn; heavy use forbidden; must identify the client | 2,000 directions/day, 40/min | None (own hardware) |
| Fit | Demo only | Demo and light public use | Production; needs a large server for a US graph |

### Geocoding (town names to coordinates)

Public **Nominatim** (OSM), cached, with an identifying User-Agent and at most one request per
second, plus the import's own place index for corridor-style town names.

## Decision (proposed)

1. **Places: A (OpenStreetMap import) + RIDB campgrounds.** Real names and locations
   nationwide. Each row records its source (`osm` or `ridb`), its OSM or RIDB id, and its licence.
2. **Prices stay honest:** `price_usd` and `points_per_night` become nullable. Hand-sourced
   prices (today's 15 hotels and 20 stops, plus any added later) are kept and preferred. Places
   without a sourced price show **"price not available"**. The planner never fills in a number.
3. **Cost and budget with unknown prices:** totals report the priced nights and count the
   unpriced ones. A budget is checked only over priced nights, and the plan warns that the total
   is partial. `validate_plan` gains an `unpriced_nights` rule so a "within budget" claim can't
   rest on unknown prices.
4. **Routing: B (OpenRouteService)** behind a provider interface, with the corridor cache kept
   as a second provider for tests and evals. Responses are cached per request.
5. **Geocoding:** Nominatim, cached, rate-limited.
6. **Scope cut:** "Anywhere" means the contiguous US on major roads. No ferries, no Alaska or
   Hawaii, no live availability.

## Consequences

- **Schema migration** `0002` (needs Adam's approval): nullable prices, new `source` values
  (`osm`, `ridb`), a `source_ref` column (`osm:node/123`, `ridb:facility/456`), and a `licence`
  column.
- **New dependency** (needs approval): a PBF filter for the import (`pyosmium`, or the
  `osmium` command-line tool used only at build time). Routing, geocoding and RIDB calls use
  the standard library's HTTP client, like the OpenAI client.
- **Two keys for Adam to create:** OpenRouteService and RIDB, both free and stored in `.env` and
  CI secrets.
- **Attribution:** "© OpenStreetMap contributors (ODbL)" and "Recreation.gov RIDB" in the UI and
  README. The derived database is ODbL share-alike if we ever publish it.
- **Evals:** the 20 corridor cases stay on the cached corridor (deterministic). New cases in 3
  more regions run against live routing in a separate, non-gating job at first, because live
  services make results vary.
- **Spec v0.7:** §1 scope, §5 rules for unpriced nights, §6.1 sourcing (OSM/RIDB allowed for
  locations; prices still only from a fetched page).
- **Effort:** about 6 PRs over 3 to 4 working days ([plan](../plans/nationwide.md)).
