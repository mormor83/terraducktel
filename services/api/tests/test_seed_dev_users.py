"""seed_dev_users.py must never put a fixed, publicly-documented password
into a production database.

Local dev (`make seed-db`, no SEED_RANDOM_PASSWORDS set) keeps the
well-known `password123` — that's the documented README/CLAUDE.md login and
every test fixture assumes it. The production bootstrap path
(`docker-entrypoint.sh`'s `TDT_BOOTSTRAP_SEED_USERS=true`) sets
SEED_RANDOM_PASSWORDS=true instead, so a real deploy gets a fresh random
password per user, printed once to the deploy log.
"""
import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch):
    """seed_dev_users builds DEV_USERS at import time from the env var, so
    each test needs a fresh import after setting/clearing it."""
    monkeypatch.delenv("SEED_RANDOM_PASSWORDS", raising=False)
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
