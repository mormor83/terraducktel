"""The `tdt login --sso` loopback hand-off on the OIDC callback.

Threat model this pins down: the hand-off must not become an open redirect. The
host is hardcoded to 127.0.0.1 and the port travels in the *signed session
cookie*, never in the callback URL — so an attacker who can make a victim visit
a crafted `/oidc/callback` cannot choose where the tokens are delivered.
"""
import re

import pytest
from fastapi import HTTPException

from app.routers.auth import _cli_handoff_response, _stash_cli_handoff
from app.schemas.auth import TokenResponse


def _pair():
    return TokenResponse(access_token="acc.tok.en", refresh_token="ref.tok.en")


# ─── session stash ─────────────────────────────────────────────────────────


def test_port_and_nonce_are_stashed_on_the_session():
    session: dict = {}
    _stash_cli_handoff(session, 49152, "a" * 32)
    assert session == {"cli_port": 49152, "cli_nonce": "a" * 32}


def test_no_port_clears_a_stale_handoff():
    """A plain browser login must not inherit an abandoned --sso hand-off."""
    session = {"cli_port": 49152, "cli_nonce": "a" * 32, "other": "keep"}
    _stash_cli_handoff(session, None, None)
    assert session == {"other": "keep"}


def test_port_without_nonce_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _stash_cli_handoff({}, 49152, None)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad", [
    "short",                      # under 16 chars
    "a" * 129,                    # over 128
    "has spaces in it aaaaaaaa",  # whitespace
    "../../etc/passwd-aaaaaaaa",  # path traversal shapes
    "a\r\nSet-Cookie: x=y-aaaa",  # header injection attempt
    "<script>alert(1)</script>",  # html
])
def test_malformed_nonces_are_rejected(bad):
    with pytest.raises(HTTPException) as exc:
        _stash_cli_handoff({}, 49152, bad)
    assert exc.value.status_code == 400


def test_nothing_is_stashed_when_the_nonce_is_rejected():
    session: dict = {}
    with pytest.raises(HTTPException):
        _stash_cli_handoff(session, 49152, "bad")
    assert session == {}


# ─── hand-off response ─────────────────────────────────────────────────────


def test_handoff_targets_loopback_only():
    resp = _cli_handoff_response(49152, "n" * 32, _pair())
    body = resp.body.decode()
    assert "http://127.0.0.1:49152/callback" in body
    # No other origin may appear as the navigation target.
    targets = re.findall(r"location\.replace\('([^']+)'\)", body)
    assert len(targets) == 1
    assert targets[0].startswith("http://127.0.0.1:49152/callback?")


def test_handoff_carries_the_pair_and_the_nonce():
    resp = _cli_handoff_response(49152, "n" * 32, _pair())
    body = resp.body.decode()
    assert "access_token=acc.tok.en" in body
    assert "refresh_token=ref.tok.en" in body
    assert "nonce=" + "n" * 32 in body


def test_handoff_is_a_200_not_a_302():
    """A 302 would put the tokens in the address bar and browser history."""
    resp = _cli_handoff_response(49152, "n" * 32, _pair())
    assert resp.status_code == 200
    assert "location" not in {k.lower() for k in resp.headers}


def test_handoff_suppresses_referrer_and_caching():
    resp = _cli_handoff_response(49152, "n" * 32, _pair())
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"
    assert "no-referrer" in resp.body.decode()


def test_handoff_uses_location_replace_not_assign():
    """`replace` leaves no history entry to navigate back to."""
    body = _cli_handoff_response(49152, "n" * 32, _pair()).body.decode()
    assert "location.replace(" in body
    assert "location.href" not in body


def test_token_values_are_url_encoded():
    """A token is base64url + dots, but never rely on that for URL safety."""
    pair = TokenResponse(access_token="a b&c=d", refresh_token="e/f?g")
    body = _cli_handoff_response(49152, "n" * 32, pair).body.decode()
    assert "a%20b%26c%3Dd" in body
    assert "e/f%3Fg" in body or "e%2Ff%3Fg" in body
    # The raw ampersand must not survive and split the query string.
    assert "access_token=a b&c=d" not in body


# ─── capability advertisement ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_config_advertises_cli_loopback(auth_client, _setup_db):
    """The CLI reads this to decide whether `--sso` can work at all.

    Without it the CLI cannot distinguish "OIDC is on" from "OIDC is on AND this
    build can hand tokens back to a loopback listener" — and guessing wrong is a
    silent five-minute hang for the user. Do not remove this key.
    """
    resp = await auth_client.get("/api/v1/auth/config")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cli_loopback"] is True
