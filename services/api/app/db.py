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


def to_sync_url(url: str) -> str:
    """asyncpg DATABASE_URL → the psycopg2 URL alembic's sync engine needs.

    asyncpg spells TLS as ``?ssl=require`` (the documented form for an
    external Postgres); libpq/psycopg2 rejects ``ssl`` as an unknown DSN
    option and wants ``sslmode`` instead, so translate it rather than make
    operators pick a spelling that breaks one of the two drivers.
    """
    from sqlalchemy.engine import make_url

    if not url.startswith("postgresql+asyncpg://"):
        return url
    u = make_url(url).set(drivername="postgresql+psycopg2")
    q = dict(u.query)
    if "ssl" in q:
        ssl = q.pop("ssl")
        q.setdefault("sslmode", ssl)
        u = u.set(query=q)
    return u.render_as_string(hide_password=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
