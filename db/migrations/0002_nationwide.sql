-- 0002_nationwide: places from OpenStreetMap and RIDB without prices (ADR-008, spec v0.7).
-- Never edit this file once it is on main; add a new migration instead.
--
-- Prices only come from a fetched page (spec §6.1): hand-sourced rows ('sample_public',
-- 'test_fixture') must have them, imported rows ('osm', 'ridb') must not. Imported rows carry
-- their upstream id in source_ref and their licence.

-- Hotels ------------------------------------------------------------------------------------
ALTER TABLE hotels
    ALTER COLUMN price_usd DROP NOT NULL,
    ALTER COLUMN points_per_night DROP NOT NULL,
    ADD COLUMN source_ref text,
    ADD COLUMN licence text;

ALTER TABLE hotels DROP CONSTRAINT hotels_source_check;
ALTER TABLE hotels
    ADD CONSTRAINT hotels_source_check
        CHECK (source IN ('sample_public', 'test_fixture', 'osm', 'ridb')),
    ADD CONSTRAINT hotels_prices_only_hand_sourced
        CHECK (source IN ('sample_public', 'test_fixture')
               OR (price_usd IS NULL AND points_per_night IS NULL)),
    ADD CONSTRAINT hotels_hand_sourced_priced
        CHECK (source NOT IN ('sample_public', 'test_fixture')
               OR (price_usd IS NOT NULL AND points_per_night IS NOT NULL)),
    ADD CONSTRAINT hotels_source_ref
        CHECK ((source NOT IN ('osm', 'ridb') OR source_ref IS NOT NULL)
               AND (source_ref IS NULL
                    OR source_ref ~ '^(osm:(node|way|relation)/[0-9]+|ridb:facility/[0-9]+)$')),
    ADD CONSTRAINT hotels_licence
        CHECK ((source = 'osm' AND licence = 'ODbL-1.0')
               OR (source = 'ridb' AND licence = 'US-PD')
               OR (source IN ('sample_public', 'test_fixture') AND licence IS NULL));

CREATE UNIQUE INDEX hotels_source_ref_key ON hotels (source_ref) WHERE source_ref IS NOT NULL;

-- Stops -------------------------------------------------------------------------------------
ALTER TABLE stops
    ALTER COLUMN est_cost_usd DROP NOT NULL,
    ADD COLUMN source_ref text,
    ADD COLUMN licence text;

ALTER TABLE stops DROP CONSTRAINT stops_source_check;
-- RIDB federal campgrounds are RV overnights (ADR-008).
ALTER TABLE stops DROP CONSTRAINT stops_type_check;
ALTER TABLE stops
    ADD CONSTRAINT stops_type_check
        CHECK (type IN ('winery', 'farm', 'brewery', 'museum', 'campground'));
ALTER TABLE stops
    ADD CONSTRAINT stops_source_check
        CHECK (source IN ('sample_public', 'test_fixture', 'osm', 'ridb')),
    ADD CONSTRAINT stops_costs_only_hand_sourced
        CHECK (source IN ('sample_public', 'test_fixture') OR est_cost_usd IS NULL),
    ADD CONSTRAINT stops_hand_sourced_costed
        CHECK (source NOT IN ('sample_public', 'test_fixture') OR est_cost_usd IS NOT NULL),
    ADD CONSTRAINT stops_source_ref
        CHECK ((source NOT IN ('osm', 'ridb') OR source_ref IS NOT NULL)
               AND (source_ref IS NULL
                    OR source_ref ~ '^(osm:(node|way|relation)/[0-9]+|ridb:facility/[0-9]+)$')),
    ADD CONSTRAINT stops_licence
        CHECK ((source = 'osm' AND licence = 'ODbL-1.0')
               OR (source = 'ridb' AND licence = 'US-PD')
               OR (source IN ('sample_public', 'test_fixture') AND licence IS NULL));

CREATE UNIQUE INDEX stops_source_ref_key ON stops (source_ref) WHERE source_ref IS NOT NULL;
