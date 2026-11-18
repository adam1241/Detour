# Seed data

Sample data for the one v1 corridor (spec §6). `hotels.csv` and `stops.csv` hold the real sample rows; `fixtures/` holds 5 synthetic rows for tests only.

## Sourcing rules (spec §6.1, agreed 2026-11-18)

- A row is drafted only from a page that was actually fetched. Its URL goes in `source_url` (`https://` only). Adam reviews every row before merge.
- `name`, address and `amenities` come from that page.
- **Coordinates:** geocoded from the page's address with OpenStreetMap Nominatim (within its usage policy).
- **Hotel `price_usd` and `points_per_night`:** hotel pages don't show fetchable rates, so these come from the tier table below, set by Adam. They are sample values, not real rates.
- **Stops:**
  - `overnight_ok = true` only if the business's own page advertises RV or camping overnight stays. Never infer or claim Harvest Hosts membership.
  - `est_cost_usd` is a price published on the page (campsite fee, tasting, admission), or 0 if the page says the visit is free. A place with no published price is skipped.
- All real rows use `source = sample_public`. `test_fixture` rows are refused by the loader outside tests.

## Price and points tiers (set by Adam)

| brand_segment | price_usd | points_per_night |
|---|---|---|
| economy | _TBD by Adam_ | _TBD by Adam_ |
| midscale | _TBD by Adam_ | _TBD by Adam_ |
| upscale | _TBD by Adam_ | _TBD by Adam_ |

## CSV format

- `hotels.csv`: `id,name,brand_segment,lon,lat,price_usd,points_per_night,amenities,source,source_url` (amenities `;`-separated)
- `stops.csv`: `id,name,type,lon,lat,overnight_ok,est_cost_usd,description,source,source_url` (`overnight_ok` is `true`/`false`)
- IDs: `hotel_<snake>` / `stop_<snake>`. Points must fall inside the corridor bbox (lon −83.0 to −76.8, lat 35.3 to 39.2).
