"""Token handling in the HTTP client — the behaviour that removes the hourly paste."""
import json
import time

import httpx
import pytest

from tdt import credentials as creds
from tdt.client import Client
from tdt.config import Profile
from tdt.errors import ExitCode, TdtError


def _jwt(exp_offset: float, kind: str = "access") -> str:
    """A structurally-valid unsigned JWT — the client only reads its claims."""
    import base64

    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'sub': 'u1', 'type': kind, 'exp': time.time() + exp_offset, 'email': 'a@b.c'})}.x"


@pytest.fixture
def profile():
    return Profile(name="test", url="https://tdt.example.com/api/v1", bu="home")


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("TDT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("TDT_TOKEN", raising=False)


def test_expiring_access_token_is_refreshed_before_the_request(profile):
    """Proactive refresh — a long apply must not die when the token lapses."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(200, json={
                "access_token": _jwt(3600), "refresh_token": _jwt(86400, "refresh"),
            })
        return httpx.Response(200, json=[{"id": "ws1"}])

    cred = creds.Credential(kind="jwt", access_token=_jwt(30), refresh_token=_jwt(86400, "refresh"))
    client = Client(profile, cred, transport=httpx.MockTransport(handler))

    client.get("/workspaces")
    assert any("/auth/refresh" in c for c in calls), "expected a proactive refresh"
    assert calls[-1].endswith("/workspaces")


def test_comfortable_access_token_is_not_refreshed(profile):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=[])

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200), refresh_token=_jwt(86400, "refresh"))
    Client(profile, cred, transport=httpx.MockTransport(handler)).get("/workspaces")
    assert not any("refresh" in c for c in calls)


def test_refresh_only_credential_redeems_on_first_use(profile):
    """The `--paste` flow stores just the refresh half — it must still work."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(200, json={
                "access_token": _jwt(3600), "refresh_token": _jwt(86400, "refresh"),
            })
        return httpx.Response(200, json=[])

    cred = creds.Credential(kind="jwt", refresh_token=_jwt(86400, "refresh"))
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    client.get("/workspaces")
    assert calls[0].endswith("/auth/refresh")
    assert client.cred.access_token, "a new access token should be stored"


def test_401_triggers_one_refresh_and_retry(profile):
    seen = {"workspaces": 0, "refresh": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/refresh"):
            seen["refresh"] += 1
            return httpx.Response(200, json={
                "access_token": _jwt(3600), "refresh_token": _jwt(86400, "refresh"),
            })
        seen["workspaces"] += 1
        return httpx.Response(401 if seen["workspaces"] == 1 else 200, json=[])

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200), refresh_token=_jwt(86400, "refresh"))
    Client(profile, cred, transport=httpx.MockTransport(handler)).get("/workspaces")
    assert seen == {"workspaces": 2, "refresh": 1}


def test_persistent_401_becomes_exit_code_2(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(401, json={"detail": "Invalid or expired refresh token"})
        return httpx.Response(401, json={"detail": "Not authenticated"})

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200), refresh_token=_jwt(86400, "refresh"))
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    with pytest.raises(TdtError) as exc:
        client.get("/workspaces")
    assert exc.value.code == ExitCode.AUTH
    assert "tdt login" in (exc.value.hint or "")


def test_api_key_401_says_the_key_is_bad_not_to_log_in(profile):
    """An API key has nothing to refresh — the advice must differ."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert not request.url.path.endswith("/auth/refresh"), "API keys must not refresh"
        return httpx.Response(401, json={"detail": "Invalid or revoked API key"})

    cred = creds.Credential(kind="api_key", api_key="tdt_abc")
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    with pytest.raises(TdtError) as exc:
        client.get("/workspaces")
    assert exc.value.code == ExitCode.AUTH
    assert "revoked or expired" in (exc.value.hint or "")


def test_api_key_403_blames_capability_not_revocation():
    """A read-capability key doing a write is a scope problem, not a dead key."""
    profile = Profile(name="test", url="https://tdt.example.com/api/v1", bu="home")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Insufficient capability"})

    cred = creds.Credential(kind="api_key", api_key="tdt_abc")
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    with pytest.raises(TdtError) as exc:
        client.put("/workspaces/x", {"repo_ref": "main"})
    assert exc.value.code == ExitCode.AUTH
    assert "capability" in (exc.value.hint or "")
    assert "revoked" not in (exc.value.hint or "")


def test_jwt_403_does_not_tell_you_to_log_in_again():
    """A role/BU denial is not fixed by re-authenticating — don't suggest it."""
    profile = Profile(name="test", url="https://tdt.example.com/api/v1", bu="home")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Requires role admin"})

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200))
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    with pytest.raises(TdtError) as exc:
        client.delete("/workspaces/x")
    assert "will not change it" in (exc.value.hint or "")
    assert "tdt login" not in (exc.value.hint or "")


def test_api_key_does_not_send_the_bu_header(profile):
    """API keys force their own BU server-side; sending the header would mislead."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=[])

    cred = creds.Credential(kind="api_key", api_key="tdt_abc")
    Client(profile, cred, transport=httpx.MockTransport(handler)).get("/workspaces")
    assert "x-business-unit" not in {k.lower() for k in seen}


def test_jwt_sends_the_bu_header(profile):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=[])

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200))
    Client(profile, cred, transport=httpx.MockTransport(handler)).get("/workspaces")
    assert seen["x-business-unit"] == "home"


def test_missing_credential_is_exit_2_with_a_login_hint(profile):
    client = Client(profile, None, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(TdtError) as exc:
        client.get("/workspaces")
    assert exc.value.code == ExitCode.AUTH
    assert "tdt login" in (exc.value.hint or "")


def test_connect_error_blames_the_network_not_auth(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200))
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    with pytest.raises(TdtError) as exc:
        client.get("/workspaces")
    assert exc.value.code == ExitCode.API
    assert "VPN" in (exc.value.hint or "")


def test_validation_errors_are_flattened_for_humans(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": [
            {"loc": ["query", "limit"], "msg": "Input should be less than or equal to 1000"},
        ]})

    cred = creds.Credential(kind="jwt", access_token=_jwt(7200))
    client = Client(profile, cred, transport=httpx.MockTransport(handler))
    with pytest.raises(TdtError) as exc:
        client.get("/runs")
    assert "query.limit" in exc.value.message
