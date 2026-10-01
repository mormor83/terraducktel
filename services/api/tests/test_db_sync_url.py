"""alembic's sync URL: asyncpg's `?ssl=require` (the documented form for an
external Postgres) must become libpq's `sslmode=require`, or migrations fail
with psycopg2's "invalid connection option" on boot."""
import pytest

from app.db import to_sync_url


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "postgresql+asyncpg://u:p@db.example:5432/tdt?ssl=require",
            "postgresql+psycopg2://u:p@db.example:5432/tdt?sslmode=require",
        ),
        (
            "postgresql+asyncpg://u:p@db/tdt",
            "postgresql+psycopg2://u:p@db/tdt",
        ),
        (
            # explicit sslmode wins over a stray ssl=
            "postgresql+asyncpg://u:p@db/tdt?ssl=require&sslmode=verify-full",
            "postgresql+psycopg2://u:p@db/tdt?sslmode=verify-full",
        ),
        (
            # URL-encoded password survives untouched
            "postgresql+asyncpg://u:p%40ss@db/tdt?ssl=require",
            "postgresql+psycopg2://u:p%40ss@db/tdt?sslmode=require",
        ),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_to_sync_url(url, expected):
    assert to_sync_url(url) == expected
