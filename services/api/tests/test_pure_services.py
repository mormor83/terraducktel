"""Unit coverage for the leaf service modules: plan_summary, secret_scanner,
runtime_settings, and the slack HTTP wrapper (httpx mocked)."""
import json

import pytest

from app.services import plan_summary as ps
from app.services import secret_scanner as ss
from app.services import runtime_settings as rs
from app.services import slack


# ─── plan_summary ────────────────────────────────────────────────────────────


def _plan(*action_lists):
    return json.dumps(
        {"resource_changes": [{"change": {"actions": a}} for a in action_lists]}
    )


def test_summarize_counts_each_action_kind():
    s = ps.summarize_plan_json(
        _plan(["create"], ["update"], ["delete"], ["create", "delete"], ["no-op"], ["read"])
    )
    assert (s.add, s.change, s.destroy, s.no_op, s.read) == (2, 1, 2, 1, 1)
    # replace adds to both add + destroy; real changes present → not no-changes.
    assert s.is_no_changes is False


def test_summarize_no_changes_and_unknown_action():
    s = ps.summarize_plan_json(_plan(["bogus"]))
    assert s.is_no_changes is True
    assert (s.add, s.change, s.destroy) == (0, 0, 0)


@pytest.mark.parametrize("bad", [None, "", "not-json{", "[]"])
def test_summarize_missing_or_malformed_returns_zeros(bad):
    s = ps.summarize_plan_json(bad)
    assert s == ps.PlanSummary()
    assert s.is_no_changes is True


def test_summarize_tolerates_null_resource_changes_and_rows():
    s = ps.summarize_plan_json(json.dumps({"resource_changes": [None, {}]}))
    assert s.is_no_changes is True


# ─── secret_scanner ──────────────────────────────────────────────────────────


def test_scanner_passes_clean_state():
    ok, reason = ss.scan_terraform_state_json({"resources": [{"name": "vpc"}]})
    assert ok is True and reason is None


def test_scanner_flags_aws_access_key():
    ok, reason = ss.scan_terraform_state_json({"k": "AKIAIOSFODNN7EXAMPLE"})
    assert ok is False and "AWS access key" in reason


def test_scanner_flags_private_key():
    ok, reason = ss.scan_terraform_state_json(
        {"k": "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"}
    )
    assert ok is False and "private key" in reason


def test_scanner_rejects_non_serializable():
    ok, reason = ss.scan_terraform_state_json({"s": {1, 2, 3}})
    # default=str makes sets serializable, so this is actually clean; use a key
    # that truly can't serialize: a non-string dict key with a custom object.
    assert ok is True


def test_scanner_rejects_truly_unserializable():
    class Boom:
        def __repr__(self):
            raise TypeError("nope")

    # json.dumps(default=str) calls str() on Boom → repr raises → TypeError path.
    ok, reason = ss.scan_terraform_state_json({"k": Boom()})
    assert ok is False and "not JSON-serializable" in reason


# ─── runtime_settings ────────────────────────────────────────────────────────


def test_coerce_returns_default_on_none_and_bad():
    assert rs._coerce(5, None) == 5
    assert rs._coerce(2.0, "abc") == 2.0
    assert rs._coerce(7, "9") == 9 and isinstance(rs._coerce(7, "9"), int)
    assert rs._coerce(1.5, "2.5") == 2.5


async def test_get_value_default_then_set_then_read(db_session):
    # Unknown key raises.
    with pytest.raises(KeyError):
        await rs.get_value(db_session, "nope.key")
    # Default before any set.
    assert await rs.get_value(db_session, "drift.interval_seconds") == 300
    # Set + read back (type preserved).
    await rs.set_value(db_session, "drift.interval_seconds", 120, updated_by="admin")
    assert await rs.get_value(db_session, "drift.interval_seconds") == 120


async def test_set_value_validates_key_and_positivity(db_session):
    with pytest.raises(KeyError):
        await rs.set_value(db_session, "nope", 1)
    with pytest.raises(ValueError):
        await rs.set_value(db_session, "drift.interval_seconds", 0)


async def test_get_all_returns_value_and_default(db_session):
    await rs.set_value(db_session, "worker.poll_interval_seconds", 5.0)
    allv = await rs.get_all(db_session)
    assert allv["worker.poll_interval_seconds"] == {"value": 5.0, "default": 2.0}
    assert set(allv) == set(rs.DEFAULTS)


# ─── slack (httpx mocked) ────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    """Stands in for httpx.AsyncClient. `handler(method, path, body)` -> dict."""

    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        return _FakeResp(self._handler("POST", url, json))

    async def get(self, url, headers=None, params=None):
        return _FakeResp(self._handler("GET", url, params))


def _install(monkeypatch, handler):
    monkeypatch.setattr(slack.httpx, "AsyncClient", lambda *a, **k: _FakeClient(handler))


async def test_verify_token_returns_identity(monkeypatch):
    _install(
        monkeypatch,
        lambda m, url, body: {
            "ok": True,
            "team": "Acme",
            "team_id": "T1",
            "bot_user_id": "B1",
            "url": "https://acme.slack.com",
        },
    )
    ident = await slack.verify_token("xoxb-1")
    assert ident.team == "Acme" and ident.bot_user_id == "B1"


async def test_verify_token_falls_back_to_user_id(monkeypatch):
    _install(monkeypatch, lambda m, url, body: {"ok": True, "user_id": "U9"})
    ident = await slack.verify_token("t")
    assert ident.bot_user_id == "U9"


async def test_post_raises_slackerror_on_not_ok(monkeypatch):
    _install(monkeypatch, lambda m, url, body: {"ok": False, "error": "invalid_auth"})
    with pytest.raises(slack.SlackError) as ei:
        await slack.verify_token("bad")
    assert ei.value.code == "invalid_auth"


async def test_get_raises_slackerror_default_code(monkeypatch):
    _install(monkeypatch, lambda m, url, params: {"ok": False})
    with pytest.raises(slack.SlackError) as ei:
        await slack.list_channels("t")
    assert ei.value.code == "unknown"


async def test_list_channels_follows_cursor(monkeypatch):
    calls = {"n": 0}

    def handler(method, url, params):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": True,
                "channels": [{"id": "C1", "name": "general", "is_private": False}],
                "response_metadata": {"next_cursor": "page2"},
            }
        return {
            "ok": True,
            "channels": [{"id": "C2", "name": "secret", "is_private": True}],
            "response_metadata": {"next_cursor": ""},
        }

    _install(monkeypatch, handler)
    chans = await slack.list_channels("t")
    assert [c.id for c in chans] == ["C1", "C2"]
    assert chans[1].is_private is True
    assert calls["n"] == 2


async def test_post_message_with_and_without_blocks(monkeypatch):
    seen = []

    def handler(method, url, body):
        seen.append(body)
        return {"ok": True}

    _install(monkeypatch, handler)
    await slack.post_message("t", "C1", "hi")
    await slack.post_message("t", "C1", "hi", blocks=[{"type": "section"}])
    assert "blocks" not in seen[0]
    assert seen[1]["blocks"] == [{"type": "section"}]


def test_slackerror_message_defaults_to_code():
    assert str(slack.SlackError("rate_limited")) == "rate_limited"
