import os
import warnings as _warnings

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

_raw_db_url = os.environ.get("DATABASE_URL")
if _raw_db_url is None:
    _warnings.warn(
        "DATABASE_URL not set — using in-memory SQLite. "
        "This is only safe for tests. Set DATABASE_URL in production.",
        stacklevel=2,
    )
    DATABASE_URL = "sqlite+aiosqlite:///:memory:"
else:
    DATABASE_URL = _raw_db_url

# Pool sizing: 10 + 20 overflow handles the API request load + the in-process
# worker loop + the reaper + the drift-detector all comfortably. Without
# pool_pre_ping a stale connection (e.g. after a Postgres restart) crashes the
# first request after recovery. SQLite (test mode) ignores pool args.
_is_sqlite = DATABASE_URL.startswith("sqlite")
_engine_kwargs: dict = {"echo": False}
if not _is_sqlite:
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
