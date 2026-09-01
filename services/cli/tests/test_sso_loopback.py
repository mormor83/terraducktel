"""The `--sso` loopback flow.

The listener is the security-relevant part: it must accept exactly the callback
this process started (nonce match) and refuse anything else, rather than storing
whatever tokens arrive on the port.
"""
import re
import threading
import urllib.parse
import urllib.request

import pytest

from tdt import sso
from tdt.config import Profile
from tdt.errors import ExitCode, TdtError


@pytest.fixture
def profile():
    return Profile(name="test", url="http://tdt.example.com/api/v1", bu="home")


def _drive(profile, respond, monkeypatch):
    """Run login_sso while a fake 'browser' hits the loopback listener.

    `respond(auth_url)` receives the URL the CLI wanted to open and returns the
    query string to deliver to /callback (or None to deliver nothing).
    """
    captured = {}

    def fake_open(url):
        captured["url"] = url

        def hit():
            query = respond(url)
            if query is None:
                return
            port = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["cli_port"][0])
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{query}", timeout=5).read()
            except Exception:  # noqa: BLE001 — a 4xx is a valid outcome here
                pass

        threading.Thread(target=hit, daemon=True).start()
        return True

    monkeypatch.setattr(sso.webbrowser, "open", fake_open)
    monkeypatch.setattr(sso, "_TIMEOUT_SECONDS", 10)
    return captured


def test_happy_path_stores_the_pair(profile, monkeypatch):
    def respond(url):
        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["cli_nonce"][0]
        return urllib.parse.urlencode({
            "access_token": "acc-123", "refresh_token": "ref-456", "nonce": nonce,
        })

    captured = _drive(profile, respond, monkeypatch)
    cred = sso.login_sso(profile)

    assert cred.kind == "jwt"
    assert cred.access_token == "acc-123"
    assert cred.refresh_token == "ref-456"
    assert captured["url"].startswith("http://tdt.example.com/api/v1/auth/oidc/login?")


def test_login_url_carries_a_strong_nonce_and_a_bound_port(profile, monkeypatch):
    seen = {}

    def respond(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        seen.update({k: v[0] for k, v in q.items()})
        return urllib.parse.urlencode({
            "access_token": "a", "refresh_token": "r", "nonce": q["cli_nonce"][0],
        })

    _drive(profile, respond, monkeypatch)
    sso.login_sso(profile)

    assert 1024 <= int(seen["cli_port"]) <= 65535
    # The API validates 16-128 url-safe chars; stay inside that.
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,128}", seen["cli_nonce"])


def test_wrong_nonce_is_refused_and_no_credential_is_returned(profile, monkeypatch):
    """A callback the CLI didn't initiate must not be trusted."""
    def respond(url):
        return urllib.parse.urlencode({
            "access_token": "attacker", "refresh_token": "attacker", "nonce": "x" * 32,
        })

    _drive(profile, respond, monkeypatch)
    with pytest.raises(TdtError) as exc:
        sso.login_sso(profile)
    assert exc.value.code == ExitCode.AUTH
    assert "nonce" in exc.value.message


def test_callback_without_tokens_is_an_error(profile, monkeypatch):
    def respond(url):
        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["cli_nonce"][0]
        return urllib.parse.urlencode({"nonce": nonce})

    _drive(profile, respond, monkeypatch)
    with pytest.raises(TdtError) as exc:
        sso.login_sso(profile)
    assert exc.value.code == ExitCode.AUTH
    assert "token pair" in exc.value.message


def test_timeout_is_exit_6_with_a_fallback_hint(profile, monkeypatch):
    _drive(profile, lambda url: None, monkeypatch)
    monkeypatch.setattr(sso, "_TIMEOUT_SECONDS", 1)
    with pytest.raises(TdtError) as exc:
        sso.login_sso(profile)
    assert exc.value.code == ExitCode.TIMEOUT
    assert "--api-key" in (exc.value.hint or "")


def test_no_browser_mode_prints_the_url_instead_of_opening_one(profile, monkeypatch, capsys):
    """Headless / SSH: we must not silently hang waiting on a browser we can't open."""
    opened = {"called": False}
    monkeypatch.setattr(sso.webbrowser, "open", lambda url: opened.update(called=True))
    monkeypatch.setattr(sso, "_TIMEOUT_SECONDS", 1)

    with pytest.raises(TdtError):
        sso.login_sso(profile, open_browser=False)

    assert opened["called"] is False
    assert "auth/oidc/login" in capsys.readouterr().out


def test_listener_is_closed_after_a_failed_login(profile, monkeypatch):
    """A timed-out login must not leave the port bound."""
    captured = _drive(profile, lambda url: None, monkeypatch)
    monkeypatch.setattr(sso, "_TIMEOUT_SECONDS", 1)
    with pytest.raises(TdtError):
        sso.login_sso(profile)

    port = int(urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)["cli_port"][0])
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # raises if the listener is still up


def test_unknown_path_on_the_listener_is_a_404(profile, monkeypatch):
    """Only /callback is served; anything else must not be treated as a callback."""
    outcome = {}

    def respond(url):
        port = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["cli_port"][0])
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/whatever", timeout=5)
        except urllib.error.HTTPError as exc:
            outcome["status"] = exc.code
        # then complete properly so login_sso returns
        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["cli_nonce"][0]
        return urllib.parse.urlencode({
            "access_token": "a", "refresh_token": "r", "nonce": nonce,
        })

    _drive(profile, respond, monkeypatch)
    sso.login_sso(profile)
    assert outcome["status"] == 404


# ─── version-skew guard ────────────────────────────────────────────────────


def test_ensure_supported_passes_when_the_capability_is_present():
    sso.ensure_supported({"oidc_enabled": True, "cli_loopback": True})


@pytest.mark.parametrize("cfg", [
    {},                                          # very old build
    {"oidc_enabled": True},                      # OIDC on, no loopback support
    {"oidc_enabled": True, "cli_loopback": False},
])
def test_ensure_supported_refuses_an_older_deployment(cfg):
    """The failure mode this prevents: a silent five-minute hang.

    An older /oidc/login ignores cli_port and redirects the browser to the SPA,
    so the human sees a successful UI login while the CLI waits out its whole
    timeout for a callback that is never sent.
    """
    with pytest.raises(TdtError) as exc:
        sso.ensure_supported(cfg)
    assert exc.value.code == ExitCode.USAGE
    assert "--api-key" in (exc.value.hint or "")


def test_guard_runs_before_any_listener_is_bound(monkeypatch):
    """Refusing early must not leave a socket bound or open a browser."""
    opened = {"called": False}
    monkeypatch.setattr(sso.webbrowser, "open", lambda url: opened.update(called=True))
    with pytest.raises(TdtError):
        sso.ensure_supported({"oidc_enabled": True})
    assert opened["called"] is False
