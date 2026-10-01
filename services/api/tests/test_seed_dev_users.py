"""seed_dev_users.py must never put a fixed, publicly-documented password
into a production database.

Local dev (`make seed-db`, no SEED_RANDOM_PASSWORDS set) keeps the
well-known `password123` — that's the documented README/CLAUDE.md login and
every test fixture assumes it. The production bootstrap path
(`docker-entrypoint.sh`'s `TDT_BOOTSTRAP_SEED_USERS=true`) sets
SEED_RANDOM_PASSWORDS=true instead, so a real deploy gets a fresh random
password per user, printed once to the deploy log.

SEED_PASSWORD (a provisioner-held secret) applies to admin@test.com only and
must never be printed; it cannot be combined with SEED_RANDOM_PASSWORDS.
"""
import asyncio
import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch):
    """seed_dev_users builds DEV_USERS at import time from the env var, so
    each test needs a fresh import after setting/clearing it."""
    monkeypatch.delenv("SEED_RANDOM_PASSWORDS", raising=False)
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    sys.modules.pop("scripts.seed_dev_users", None)
    yield
    sys.modules.pop("scripts.seed_dev_users", None)


def _import_fresh():
    import scripts.seed_dev_users as mod
    return importlib.reload(mod)


def test_default_uses_documented_dev_password():
    mod = _import_fresh()
    passwords = {email: password for email, password, _role in mod.DEV_USERS}
    assert passwords == {
        "admin@test.com": "password123",
        "operator@test.com": "password123",
        "viewer@test.com": "password123",
    }


def test_random_passwords_enabled_generates_distinct_non_default_passwords(monkeypatch):
    monkeypatch.setenv("SEED_RANDOM_PASSWORDS", "true")
    mod = _import_fresh()
    passwords = [password for _email, password, _role in mod.DEV_USERS]
    assert len(set(passwords)) == 3, "each user must get its own random password"
    assert "password123" not in passwords
    assert all(len(p) >= 16 for p in passwords)


FIXED = "provisioner-held-secret-42"  # >= 16 chars


def test_seed_password_applies_to_admin_only(monkeypatch):
    monkeypatch.setenv("SEED_PASSWORD", FIXED)
    mod = _import_fresh()
    passwords = {email: pw for email, pw, _r in mod.DEV_USERS}
    assert passwords["admin@test.com"] == FIXED
    op, vw = passwords["operator@test.com"], passwords["viewer@test.com"]
    assert FIXED not in (op, vw)
    assert op != vw, "operator and viewer must get distinct random passwords"
    assert "password123" not in (op, vw)
    assert len(op) >= 16 and len(vw) >= 16
    assert mod.GENERATED_PASSWORD_EMAILS == {"operator@test.com", "viewer@test.com"}


def test_seed_password_and_random_together_exit_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("SEED_RANDOM_PASSWORDS", "true")
    monkeypatch.setenv("SEED_PASSWORD", FIXED)
    with pytest.raises(SystemExit) as exc:
        _import_fresh()
    assert exc.value.code not in (0, None)
    captured = capsys.readouterr()
    assert "mutually exclusive" in captured.err
    assert FIXED not in captured.out + captured.err


def test_short_seed_password_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("SEED_PASSWORD", "too-short-15chr")  # 15 chars
    with pytest.raises(SystemExit) as exc:
        _import_fresh()
    assert exc.value.code not in (0, None)
    captured = capsys.readouterr()
    assert "at least 16" in captured.err
    assert "too-short-15chr" not in captured.out + captured.err


def test_empty_seed_password_is_ignored(monkeypatch):
    monkeypatch.setenv("SEED_PASSWORD", "")
    mod = _import_fresh()
    assert {pw for _e, pw, _r in mod.DEV_USERS} == {"password123"}
    assert mod.GENERATED_PASSWORD_EMAILS == frozenset()


@pytest.mark.parametrize("padded", [f" {FIXED}", f"{FIXED} ", f"{FIXED}\n", f"\t{FIXED}", "   "])
def test_seed_password_with_surrounding_whitespace_is_rejected_not_stripped(
    monkeypatch, capsys, padded
):
    """Silently stripping would set a different password than the provisioner
    holds (and then lock them out), so refuse instead."""
    monkeypatch.setenv("SEED_PASSWORD", padded)
    with pytest.raises(SystemExit) as exc:
        _import_fresh()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "whitespace" in captured.err
    assert FIXED not in captured.out + captured.err


def _seed_into_sqlite(tmp_path, monkeypatch, mod):
    """Run mod.seed() against a throwaway SQLite file with the full schema."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import Base
    import app.models.user  # noqa: F401
    import app.models.business_unit  # noqa: F401

    url = f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}"

    async def _create():
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await eng.dispose()

    asyncio.run(_create())
    monkeypatch.setenv("DATABASE_URL", url)
    return asyncio.run(mod.seed())


def test_seed_never_prints_fixed_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEED_PASSWORD", FIXED)
    mod = _import_fresh()
    assert _seed_into_sqlite(tmp_path, monkeypatch, mod) == 0
    out = capsys.readouterr().out
    assert FIXED not in out
    assert "generated password for admin@test.com" not in out
    passwords = {email: pw for email, pw, _r in mod.DEV_USERS}
    for email in ("operator@test.com", "viewer@test.com"):
        assert f"generated password for {email}: {passwords[email]}" in out
    assert "created: admin@test.com (admin)" in out


def test_seed_random_mode_prints_every_generated_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEED_RANDOM_PASSWORDS", "true")
    mod = _import_fresh()
    assert _seed_into_sqlite(tmp_path, monkeypatch, mod) == 0
    out = capsys.readouterr().out
    for email, pw, _r in mod.DEV_USERS:
        assert f"generated password for {email}: {pw}" in out


def test_seed_default_mode_prints_no_password(tmp_path, monkeypatch, capsys):
    mod = _import_fresh()
    assert _seed_into_sqlite(tmp_path, monkeypatch, mod) == 0
    out = capsys.readouterr().out
    assert "generated password" not in out
    assert "password123" not in out
