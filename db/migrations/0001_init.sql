-- 0001_init: core tables (spec v0.3 §6.2, ADR-002, ADR-003).
-- Never edit this file once it is on main; add a new migration instead.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE hotels (
    id               text PRIMARY KEY CHECK (id ~ '^hotel_[a-z0-9_]+$'),
    name             text NOT NULL CHECK (name ~ '\S'),
    brand_segment    text NOT NULL CHECK (brand_segment IN ('economy', 'midscale', 'upscale')),
    geom             geometry(Point, 4326) NOT NULL
                     CHECK (ST_X(geom) BETWEEN -180 AND 180 AND ST_Y(geom) BETWEEN -90 AND 90),
    price_usd        numeric(8, 2) NOT NULL CHECK (price_usd > 0 AND price_usd <> 'NaN'),
    points_per_night integer NOT NULL CHECK (points_per_night >= 0),
    amenities        text[] NOT NULL DEFAULT '{}',
    -- 'test_fixture' rows exist only in test databases; the loader refuses them elsewhere.
    source           text NOT NULL CHECK (source IN ('sample_public', 'test_fixture')),
    source_url       text NOT NULL CHECK (source_url ~ '^https://[^/\s]+')
);

CREATE INDEX hotels_geom_gix ON hotels USING GIST (geom);
CREATE INDEX hotels_geog_gix ON hotels USING GIST ((geom::geography));

CREATE TABLE stops (
    id           text PRIMARY KEY CHECK (id ~ '^stop_[a-z0-9_]+$'),
    name         text NOT NULL CHECK (name ~ '\S'),
    type         text NOT NULL CHECK (type IN ('winery', 'farm', 'brewery', 'museum')),
    geom         geometry(Point, 4326) NOT NULL
                 CHECK (ST_X(geom) BETWEEN -180 AND 180 AND ST_Y(geom) BETWEEN -90 AND 90),
    overnight_ok boolean NOT NULL,
    est_cost_usd numeric(8, 2) NOT NULL CHECK (est_cost_usd >= 0 AND est_cost_usd <> 'NaN'),
    description  text NOT NULL DEFAULT '',
    source       text NOT NULL CHECK (source IN ('sample_public', 'test_fixture')),
    source_url   text NOT NULL CHECK (source_url ~ '^https://[^/\s]+')
);

CREATE INDEX stops_geom_gix ON stops USING GIST (geom);
CREATE INDEX stops_geog_gix ON stops USING GIST ((geom::geography));

CREATE TABLE trips (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_text   text NOT NULL,
    vehicle        text NOT NULL CHECK (vehicle IN ('rv', 'car')),
    plan_json      jsonb,
    trace_json     jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    model          text,
    prompt_version text,
    eval_tags      text[] NOT NULL DEFAULT '{}'
);
