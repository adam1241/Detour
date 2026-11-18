# Seed data

Sample data for the one v1 corridor (spec §6).

- `hotels.csv`: **15 real Choice-brand hotels** along the corridor, with real rates for one sample night (see below). Built by `scripts/build_seed_hotels.py` from `sources/hotels_observed.csv`.
- `stops.csv`: **20 real host-style stops** (example places, not claimed Harvest Hosts members): 15 day stops with a price or free-entry statement from their own website, and 5 RV overnight hosts priced from their own Hipcamp listing. Built by `scripts/build_seed_stops.py` from `sources/stops_observed.csv`.
- `fixtures/` holds 5 synthetic rows used only by tests. They are not sample data: they're labelled "(test data)", tagged `source = test_fixture`, point at `example.com`, and the loader refuses them outside tests.

## Sourcing rules (spec §6.1, agreed 2026-11-18)

- A row is drafted only from a page that was actually fetched. Its URL goes in `source_url` (`https://` only). Adam reviews every row before merge.
- `name` and address come from that page. `amenities` is left empty for now (not captured).
- **Coordinates:** geocoded from the page's address with OpenStreetMap Nominatim (within its usage policy).
- **Hotel `price_usd` and `points_per_night`:** the real rates choicehotels.com showed for that hotel for **one sample night: Wed 14 Apr 2027 (1 night, 1 room, 2 adults)**, observed on 2026-11-18:
  - `price_usd` = the **Best Available** rate (the standard, non-member price; member discounts are not used).
  - `points_per_night` = the **Reward Points** rate (search with `ratePlanCode=SRD`, the "Use points" view). Where a hotel showed a standard and a discounted points price, the standard one is used.
  - Hotels that weren't redeemable with points that night (e.g. Suburban Studios Front Royal) or were sold out were not included.
  - These are a snapshot. Real prices change by date and demand; the UI must say "sample rate for one night".
- **Stops:**
  - `overnight_ok = true` only if the business's own website **or its own Hipcamp listing** advertises RV stays with a nightly price (Hipcamp allowed by Adam, 2026-11-18). Harvest Hosts listings are never used; never infer or claim Harvest Hosts membership.
  - For overnight hosts, `est_cost_usd` is the listing's lowest "from" nightly price for a site that allows RVs (before Hipcamp's service fee).
  - `est_cost_usd` is a price published on the page (campsite fee, tasting, admission), or 0 if the page says the visit is free. A place with no published price is skipped.
- All real rows use `source = sample_public`. `test_fixture` rows are refused by the loader outside tests.

## Brand → `brand_segment`

Choice brands mapped to our three tiers by STR chain scale:

| brand_segment | Brands in the data | Sample range (14 Apr 2027) |
|---|---|---|
| economy | Econo Lodge, Rodeway Inn | $68–$119, 8,000–10,000 pts |
| midscale | Quality Inn, Sleep Inn, Clarion Pointe | $73–$125, 10,000–20,000 pts |
| upscale | Comfort Inn, Comfort Suites (upper midscale), Ascend Collection, Cambria | $107–$195, 15,000–30,000 pts |

For comparison, 2024 U.S. average daily rates were about $89 (economy), $111 (midscale) and $149 (upper midscale) (Colliers, *2024 Hospitality Brand Performance Comparison*).

## Stops: how they were sourced (2026-11-18)

- Day stops: each place's **own website** was read (the `source_url`). Tourism sites and Harvest Hosts were not used as sources.
- RV overnight hosts: each host's **own Hipcamp listing** (the `source_url`). The listing's map pin is used for coordinates (match type `listing`); hosts may place it approximately.
- `est_cost_usd` = the lowest published adult price for the experience (tasting flight, admission, tour), or 0 where the page says entry or tours are free. Places with no published price (e.g. Grandfather Vineyard, Highland Brewing, Appalachian Mountain Brewery) were skipped.
- Judgement call: Polyface Farm's page describes open-door self-guided visits with no fee listed; it is recorded as 0.
- Mix: see the table below. No brewery published a price on its own site, so there are no `brewery` rows yet.
- `overnight_ok` is false for the 15 day stops (none advertises RV stays on its own site; Polyface explicitly forbids camping) and true for the 5 Hipcamp hosts:

| RV host | Near | RV nightly price | Notes | km from route |
|---|---|---|---|---|
| South River Farm Campground | Front Royal, VA | $59 | full-hookup RV sites, working farm | 2.3 |
| Blue Quartz Winery/Brewery | Etlan, VA (east of Luray) | $30 | RV-only vineyard sites, no hookups | 24.7 |
| Dices Spring Farm Camp & Views | Weyers Cave, VA | $45 | RV/tent, no hookups | 0.3 |
| Campsites at Shipley Farms | Vilas, NC (near Boone) | $31 | class B RVs and campervans only | 0.5 |
| Far Flung Farm | Canton, NC (west of Asheville) | $35 | RV/tent, no hookups | 25.3 |

| Type | Count | Price range |
|---|---|---|
| museum | 10 | free–$36 |
| winery | 4 | $20–$30 (tasting) / $30 (RV night) |
| farm | 6 | free (visits) / $31–$59 (RV night) |

Geocoding match per stop is in `sources/stops_geocoded.json`: `place` (the venue itself), `override` (explicit query in `stops_observed.csv`: Luray Caverns for the car museum; Markham, Fauquier County for Chateau O'Brien, whose road isn't in OSM) or `town` (Linden Vineyards: town centre, ~2 km off).

## Known gaps

- No Choice hotel redeemable with points was found in Front Royal or DC for the sample night, so the first ~110 km (DC → Front Royal) has no hotel. Trips usually spend their first night further south.
- Hotels are 0–15 km from the route (12 of 15 within 3 km); stops 0–12 km (11 of 15 within 2 km), measured against `corridor_route.geojson`.
- **Only 5 RV overnight hosts**, none between Staunton and Boone (the Roanoke stretch). RV trips through Roanoke need a hotel night there.

## CSV format

- `hotels.csv`: `id,name,brand_segment,lon,lat,price_usd,points_per_night,amenities,source,source_url` (amenities `;`-separated)
- `stops.csv`: `id,name,type,lon,lat,overnight_ok,est_cost_usd,description,source,source_url` (`overnight_ok` is `true`/`false`)
- IDs: `hotel_<snake>` / `stop_<snake>`. Points must fall inside the corridor bbox (lon −83.0 to −76.8, lat 35.3 to 39.2).
