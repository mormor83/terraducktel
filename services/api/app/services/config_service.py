import base64
import hashlib
import time
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import Config, ConfigHistory

# Cache TTL in seconds
_CACHE_TTL = 60


def _fp(v: Optional[str]) -> str:
    """Return SHA-256 fingerprint (first 8 hex chars) of a value, or 'null'."""
    if v is None:
        return "null"
    return hashlib.sha256(v.encode()).hexdigest()[:8]


def _make_fernet(encryption_key: bytes) -> Fernet:
    """Derive a Fernet instance from a raw key using HKDF.

    Requires at least 16 bytes of input key material.

    Salt history:
      - v1 (legacy): used until 2026-05-01; migration 008 wiped every row
        encrypted under it.
      - v2 (`terraducktel-config-v1`): current. If the salt ever changes again,
        write another wipe-or-rotate migration before flipping this constant.
    """
    if len(encryption_key) < 16:
        raise ValueError(
            f"CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes, got {len(encryption_key)}"
        )
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"terraducktel-config-v1",
        info=b"fernet-key",
    ).derive(encryption_key)
    return Fernet(base64.urlsafe_b64encode(derived))


class ConfigService:
    def __init__(self, session: AsyncSession, encryption_key: bytes) -> None:
        self._session = session
        self._fernet = _make_fernet(encryption_key)
        # cache: key → (decrypted_value, expiry_timestamp)
        self._cache: dict[str, tuple[str, float]] = {}

    def _encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()

    def _cache_get(self, key: str) -> Optional[str]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.time() > expiry:
            del self._cache[key]
            return None
        return value

    def _cache_set(self, key: str, value: str) -> None:
        self._cache[key] = (value, time.time() + _CACHE_TTL)

    def _cache_invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    async def get(self, key: str) -> Optional[str]:
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        row = await self._session.get(Config, key)
        if row is None:
            return None

        value = self._decrypt(row.value) if row.is_secret else row.value
        self._cache_set(key, value)
        return value

    async def set(
        self,
        key: str,
        value: str,
        is_secret: bool = False,
        description: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> None:
        # Invalidate cache so next get() re-reads from DB
        self._cache_invalidate(key)

        existing = await self._session.get(Config, key)
        stored_value = self._encrypt(value) if is_secret else value

        if existing is not None:
            # Record history — never store plaintext secrets; use fingerprint for audit trail
            if is_secret or existing.is_secret:
                old_plain = self._decrypt(existing.value) if existing.is_secret else existing.value
                history_entry = ConfigHistory(
                    key=key,
                    old_value=f"[REDACTED-{_fp(old_plain)}]",
                    new_value=f"[REDACTED-{_fp(value)}]",
                    changed_by=updated_by,
                )
            else:
                history_entry = ConfigHistory(
                    key=key,
                    old_value=existing.value,
                    new_value=value,
                    changed_by=updated_by,
                )
            self._session.add(history_entry)
            existing.value = stored_value
            existing.is_secret = is_secret
            if description is not None:
                existing.description = description
            existing.updated_by = updated_by
        else:
            row = Config(
                key=key,
                value=stored_value,
                is_secret=is_secret,
                description=description,
                updated_by=updated_by,
            )
            self._session.add(row)

        await self._session.flush()

    async def get_history(self, key: str) -> list[ConfigHistory]:
        result = await self._session.execute(
            select(ConfigHistory)
            .where(ConfigHistory.key == key)
            .order_by(ConfigHistory.changed_at)
        )
        return list(result.scalars().all())

    # ---- Business Unit scoping ----------------------------------------------
    #
    # Per-BU keys live under the namespace `bu.<slug>.<rest>`. The helpers
    # below check the BU-scoped key first and fall back to the legacy global
    # key for one release so existing deployments behave identically until
    # they re-save their settings under the BU-scoped form.

    @staticmethod
    def bu_key(bu_slug: str, key: str) -> str:
        return f"bu.{bu_slug}.{key}"

    async def get_for_bu(self, bu_slug: str, key: str) -> Optional[str]:
        """Read `bu.<slug>.<key>`; fall back to the unscoped `<key>` if unset.

        The fallback exists so workspaces in the seeded 'default' BU keep
        working with config saved before BU support landed.
        """
        scoped = await self.get(self.bu_key(bu_slug, key))
        if scoped is not None:
            return scoped
        return await self.get(key)

    async def set_for_bu(
        self,
        bu_slug: str,
        key: str,
        value: str,
        is_secret: bool = False,
        description: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> None:
        await self.set(
            self.bu_key(bu_slug, key),
            value,
            is_secret=is_secret,
            description=description,
            updated_by=updated_by,
        )

    async def delete(self, key: str) -> bool:
        """Remove a config row. Returns True if a row was deleted."""
        self._cache_invalidate(key)
        existing = await self._session.get(Config, key)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def delete_for_bu(self, bu_slug: str, key: str) -> bool:
        return await self.delete(self.bu_key(bu_slug, key))

    async def get_all(self) -> dict[str, str]:
        result = await self._session.execute(select(Config))
        rows = result.scalars().all()
        out: dict[str, str] = {}
        for row in rows:
            cached = self._cache_get(row.key)
            if cached is not None:
                out[row.key] = cached
            else:
                value = self._decrypt(row.value) if row.is_secret else row.value
                self._cache_set(row.key, value)
                out[row.key] = value
        return out
