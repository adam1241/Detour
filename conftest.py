"""Shared pytest fixtures for database tests (marker `db`; they run in CI's `spatial` job)."""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set; db tests need a PostGIS database")
    return url


@pytest.fixture
def schema(db_url: str) -> Iterator[str]:
    """A unique, empty schema owned by this test, dropped afterwards."""
    name = f"detour_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(db_url, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public")
        # Identifier quoting via psycopg's sql module; no string formatting of SQL.
        create = sql.Composed([sql.SQL("CREATE SCHEMA "), sql.Identifier(name)])
        drop = sql.Composed([sql.SQL("DROP SCHEMA "), sql.Identifier(name), sql.SQL(" CASCADE")])
        admin.execute(create)
        try:
            yield name
        finally:
            admin.execute(drop)


@pytest.fixture
def conn(db_url: str, schema: str) -> Iterator[psycopg.Connection]:
    """Autocommit connection whose search_path is the test's own schema (then public)."""
    with psycopg.connect(db_url, autocommit=True, options=f"-c search_path={schema},public") as c:
        yield c
