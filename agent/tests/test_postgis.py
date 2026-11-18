import os

import psycopg
import pytest


@pytest.mark.db
def test_postgis_extension_available() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set; db tests need a PostGIS database")
    with psycopg.connect(url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        row = conn.execute("SELECT postgis_version()").fetchone()
    assert row is not None
    assert row[0]
