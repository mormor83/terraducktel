"""Operator-tunable runtime settings backed by the encrypted config table.

We don't add new env vars. Operators tune these via the `/api/v1/runtime-config`
endpoint (admin-only) which writes to the same `config` table that backs every
other integration. Values are cached for 60s by ConfigService so the worker's
hot path doesn't query the DB on every iteration.

Settings exposed here are *operational dials*, not security invariants. The
dials affect timing and lifecycle behaviour only.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


# ─── Defaults ──────────────────────────────────────────────────────────────


DEFAULTS: dict[str, int | float] = {
    # Job-queue worker
    "worker.poll_interval_seconds": 2.0,
    "worker.stale_after_seconds": 90,
    "worker.reaper_interval_seconds": 30.0,
    # Detectors (also read by the standalone detector containers via the API).
    "drift.interval_seconds": 300,
    "liveness.interval_seconds": 300,
    "liveness.grace_seconds_after_create": 600,
    # Audit / retention
    "audit.verify_limit_rows": 10000,
    # Auth — session lifetimes for user-issued JWTs (in minutes / hours).
    # Internal machine tokens (executor callbacks, approval links) use the
    # module defaults in app/auth/jwt.py and are unaffected by these dials.
    "auth.access_token_expire_minutes": 480,  # 8h
    "auth.refresh_token_expire_hours": 24,
}


# ─── Helpers ───────────────────────────────────────────────────────────────


def _coerce(default: int | float, raw: Optional[str]) -> int | float:
    """Cast a raw config string back to the type of `default`. Returns
    `default` unchanged if `raw` is None or unparseable."""
    if raw is None:
        return default
    try:
        return type(default)(raw)
    except (ValueError, TypeError):
        logger.warning(
            "runtime_settings: cannot coerce %r to %s; using default %s",
            raw,
            type(default).__name__,
            default,
        )
        return default


async def _config(db: AsyncSession) -> ConfigService:
    return ConfigService(db, get_credential_encryption_key())


async def get_value(db: AsyncSession, key: str) -> int | float:
    """Read one runtime setting. Returns the typed value or its default."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown runtime setting: {key}")
    cs = await _config(db)
    return _coerce(DEFAULTS[key], await cs.get(key))


async def set_value(
    db: AsyncSession,
    key: str,
    value: int | float,
    *,
    updated_by: Optional[str] = None,
) -> None:
    """Persist one runtime setting. Validates the key and stringifies value."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown runtime setting: {key}")
    # Defensive: refuse non-positive intervals — accidentally setting these to
    # 0 would peg a CPU in the poll loop.
    if isinstance(value, (int, float)) and value <= 0:
        raise ValueError(f"{key} must be > 0, got {value}")
    cs = await _config(db)
    await cs.set(
        key,
        str(value),
        description=f"runtime tunable (default={DEFAULTS[key]})",
        updated_by=updated_by,
    )


async def get_all(db: AsyncSession) -> dict[str, dict]:
    """Return every setting + its default + the active value.

    Shape: { key: {"value": <int|float>, "default": <int|float>} }
    Used by the admin Settings page and the GET endpoint.
    """
    cs = await _config(db)
    out: dict[str, dict] = {}
    for k, default in DEFAULTS.items():
        raw = await cs.get(k)
        out[k] = {"value": _coerce(default, raw), "default": default}
    return out
