"""Unit coverage for ConfigService: fingerprinting, key derivation, get/set
(secret + plain), TTL cache, history, BU-scoping, delete, and get_all."""
import time

import pytest

from app.services import config_service as cs
from app.services.config_service import ConfigService

_KEY = b"test_key_exactly_32_bytes_long!!"


def test_fp_null_and_value():
    assert cs._fp(None) == "null"
    assert cs._fp("hello") == cs.hashlib.sha256(b"hello").hexdigest()[:8]


def test_make_fernet_short_key_raises():
    with pytest.raises(ValueError, match="at least 16 bytes"):
        cs._make_fernet(b"short")


def _svc(session):
    return ConfigService(session, _KEY)


async def test_get_set_plain_and_secret_roundtrip(db_session):
    svc = _svc(db_session)
    await svc.set("plain.key", "v1")
    await svc.set("secret.key", "topsecret", is_secret=True)
    # secret is stored encrypted (row.value != plaintext) but get() decrypts.
    assert await svc.get("plain.key") == "v1"
    assert await svc.get("secret.key") == "topsecret"
    # unknown key → None
    assert await svc.get("nope") is None


async def test_cache_hit_then_expiry(db_session, monkeypatch):
    svc = _svc(db_session)
    await svc.set("k", "v")
    assert await svc.get("k") == "v"  # populates cache
    # second get hits cache (no DB) — force a value change directly in DB then
    # confirm cache still returns the old value until expiry.
    row = await db_session.get(cs.Config, "k")
    row.value = "changed"
    await db_session.flush()
    assert await svc.get("k") == "v"  # served from cache
    # advance time past TTL → cache entry expires and is deleted (lines 67-69)
    _real_time = time.time
    monkeypatch.setattr(cs.time, "time", lambda: _real_time() + cs._CACHE_TTL + 1)
    assert await svc.get("k") == "changed"


async def test_set_existing_records_history_and_description(db_session):
    svc = _svc(db_session)
    await svc.set("d.key", "old", description="first")
    await svc.set("d.key", "new", description="second", updated_by="admin")
    assert await svc.get("d.key") == "new"
    hist = await svc.get_history("d.key")
    assert len(hist) == 1
    assert hist[0].old_value == "old" and hist[0].new_value == "new"
    row = await db_session.get(cs.Config, "d.key")
    assert row.description == "second"


async def test_set_existing_secret_history_is_redacted(db_session):
    svc = _svc(db_session)
    await svc.set("s.key", "oldsecret", is_secret=True)
    await svc.set("s.key", "newsecret", is_secret=True, updated_by="admin")
    hist = await svc.get_history("s.key")
    assert hist[0].old_value.startswith("[REDACTED-")
    assert hist[0].new_value.startswith("[REDACTED-")
    assert "oldsecret" not in hist[0].old_value


async def test_bu_scoping_get_set_and_fallback(db_session):
    svc = _svc(db_session)
    assert ConfigService.bu_key("acme", "github.token") == "bu.acme.github.token"
    # fallback to global when BU-scoped unset
    await svc.set("github.token", "global-tok")
    assert await svc.get_for_bu("acme", "github.token") == "global-tok"
    # BU-scoped wins once set
    await svc.set_for_bu("acme", "github.token", "bu-tok", is_secret=True)
    assert await svc.get_for_bu("acme", "github.token") == "bu-tok"


async def test_delete_and_delete_for_bu(db_session):
    svc = _svc(db_session)
    await svc.set("del.key", "v")
    assert await svc.delete("del.key") is True
    assert await svc.delete("del.key") is False  # already gone
    await svc.set_for_bu("acme", "x", "v")
    assert await svc.delete_for_bu("acme", "x") is True


async def test_get_all_mixes_cached_and_secret(db_session):
    svc = _svc(db_session)
    await svc.set("a", "1")
    await svc.set("b", "2", is_secret=True)
    await svc.get("a")  # warm cache for one key (hits cached branch in get_all)
    allv = await svc.get_all()
    assert allv["a"] == "1" and allv["b"] == "2"
