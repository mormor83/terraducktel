#!/usr/bin/env python3
"""Seed dev users matching pytest fixtures (idempotent).

Run after: alembic upgrade head

  DATABASE_URL=postgresql+asyncpg://... python scripts/seed_dev_users.py

Post-Business-Units (migration 018):
  - admin@test.com    → is_superadmin=true  (sees all BUs)
  - operator@test.com → membership on 'default' BU as 'operator'
  - viewer@test.com   → membership on 'default' BU as 'viewer'

The legacy `users.role` column is still populated for one release as a
fallback for code paths that haven't migrated to per-BU role resolution.

Password: `password123` for local dev (matches the documented README/
CLAUDE.md login and every test fixture that assumes it). Set
`SEED_RANDOM_PASSWORDS=true` to generate a fresh random password per user
instead and print it once to stdout — this is what `docker-entrypoint.sh`'s
production bootstrap path (`TDT_BOOTSTRAP_SEED_USERS=true`) does, so a
production deploy never creates a well-known, publicly-documented password
for a superadmin account. Capture the printed passwords from the deploy
logs; they are not stored or shown again.

`SEED_PASSWORD=<value>` (>= 16 chars) sets a known password for
`admin@test.com` ONLY, for provisioners that already hold the secret. That
password is never printed. operator@test.com / viewer@test.com still get a
fresh random password each, printed once like SEED_RANDOM_PASSWORDS does.
SEED_PASSWORD and SEED_RANDOM_PASSWORDS are mutually exclusive: setting both
exits non-zero rather than guess which one the operator meant (a fixed
password printed under a "generated … shown once" label in a deploy log is
exactly the leak this script exists to avoid).
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
import uuid
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Ensure app is importable when run as script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.jwt import hash_password  # noqa: E402
from app.models.business_unit import DEFAULT_BU_ID, UserBusinessUnit  # noqa: E402
from app.models.user import User  # noqa: E402

DEV_EMAILS_ROLES: tuple[tuple[str, str], ...] = (
    ("admin@test.com", "admin"),
    ("operator@test.com", "operator"),
    ("viewer@test.com", "viewer"),
)


def _random_passwords_enabled() -> bool:
    return os.environ.get("SEED_RANDOM_PASSWORDS", "false").strip().lower() in (
        "true", "1", "yes",
    )


SEED_PASSWORD_MIN_LEN = 16
FIXED_PASSWORD_EMAIL = "admin@test.com"


def _fail(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _build_dev_users() -> tuple[tuple[tuple[str, str, str], ...], frozenset[str]]:
    """((email, password, role) triples, emails whose password was generated).

    Modes (the password is only ever applied when a user row is freshly
    created — see seed() below; an existing user's password is never touched):

    - SEED_PASSWORD=<pw>: admin@test.com gets <pw> (never printed);
      operator/viewer get fresh random passwords (printed once).
    - SEED_RANDOM_PASSWORDS=true: every user gets a fresh random password
      (printed once).
    - neither: the documented dev `password123` for everyone.

    Both at once, or a SEED_PASSWORD shorter than SEED_PASSWORD_MIN_LEN, is
    refused with a non-zero exit.
    """
    fixed = os.environ.get("SEED_PASSWORD", "").strip()
    if fixed:
        if _random_passwords_enabled():
            _fail(
                "SEED_PASSWORD and SEED_RANDOM_PASSWORDS are mutually exclusive — "
                "unset one of them."
            )
        if len(fixed) < SEED_PASSWORD_MIN_LEN:
            _fail(
                f"SEED_PASSWORD must be at least {SEED_PASSWORD_MIN_LEN} characters "
                f"(got {len(fixed)})."
            )
        users = tuple(
            (email, fixed if email == FIXED_PASSWORD_EMAIL else secrets.token_urlsafe(18), role)
            for email, role in DEV_EMAILS_ROLES
        )
        generated = frozenset(e for e, _r in DEV_EMAILS_ROLES if e != FIXED_PASSWORD_EMAIL)
        return users, generated
    if not _random_passwords_enabled():
        return tuple((email, "password123", role) for email, role in DEV_EMAILS_ROLES), frozenset()
    return (
        tuple((email, secrets.token_urlsafe(18), role) for email, role in DEV_EMAILS_ROLES),
        frozenset(e for e, _r in DEV_EMAILS_ROLES),
    )


DEV_USERS: tuple[tuple[str, str, str], ...]
# Emails whose password was randomly generated in this invocation. Only these
# are ever printed; an operator-supplied SEED_PASSWORD never is.
GENERATED_PASSWORD_EMAILS: frozenset[str]
DEV_USERS, GENERATED_PASSWORD_EMAILS = _build_dev_users()


async def seed() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        return 1

    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        for email, password, role in DEV_USERS:
            r = await session.execute(select(User).where(User.email == email))
            existing = r.scalars().first()
            if existing is not None:
                # Idempotent backfill: if the user pre-dates BU support, make
                # sure their flags + memberships are correct.
                changed = False
                if role == "admin" and not existing.is_superadmin:
                    existing.is_superadmin = True
                    changed = True
                if role in ("operator", "viewer"):
                    m = await session.execute(
                        select(UserBusinessUnit).where(
                            UserBusinessUnit.user_id == existing.id,
                            UserBusinessUnit.business_unit_id == DEFAULT_BU_ID,
                        )
                    )
                    if m.scalars().first() is None:
                        session.add(
                            UserBusinessUnit(
                                user_id=existing.id,
                                business_unit_id=DEFAULT_BU_ID,
                                role=role,
                            )
                        )
                        changed = True
                if changed:
                    print(f"updated: {email} ({role})")
                else:
                    print(f"skip (exists): {email}")
                continue

            user_id = str(uuid.uuid4())
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password(password),
                    role=role,
                    is_superadmin=(role == "admin"),
                    auth_provider="local",
                )
            )
            if email in GENERATED_PASSWORD_EMAILS:
                # Only ever printed for a row we just created with a password
                # generated right here — never for the "exists" branch above
                # (the real current password could be anything from an earlier
                # run) and never for an operator-supplied SEED_PASSWORD.
                print(f"generated password for {email}: {password} (shown once, not stored anywhere)")
            # Flush the User row before queueing its UBU. Both rows share a
            # session, and SQLAlchemy's autoflush doesn't reliably order them
            # by FK dependency here — without an explicit flush the UBU insert
            # can fire first and trip user_business_units_user_id_fkey.
            await session.flush()
            if role in ("operator", "viewer"):
                session.add(
                    UserBusinessUnit(
                        user_id=user_id,
                        business_unit_id=DEFAULT_BU_ID,
                        role=role,
                    )
                )
                await session.flush()
            print(f"created: {email} ({role})")
        await session.commit()

    await engine.dispose()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(seed()))
