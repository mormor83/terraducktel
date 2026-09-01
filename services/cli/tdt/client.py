"""Thin HTTP client over the TDT API, with transparent token refresh.

Two behaviours worth knowing about:

* **Proactive refresh.** Before each request, if the stored access token expires
  within `_REFRESH_SKEW` seconds, the client redeems the refresh token first.
  That keeps a long `tdt run apply --wait` from dying an hour in — the failure
  mode `tdt-flow.sh` documented and could not fix.
* **Reactive refresh.** A 401 triggers one refresh-and-retry. If that also
  fails, you get exit code 2 and a pointer to `tdt login`.

API keys never refresh (there is nothing to refresh) — a 401 on a `tdt_` key
means it was revoked or expired, and the error says so.
"""
from __future__ import annotations

from typing import Any

import httpx

from . import credentials as creds
from .config import Profile
from .errors import ExitCode, TdtError

_REFRESH_SKEW = 120.0  # refresh when this close to expiry
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class Client:
    def __init__(
        self,
        profile: Profile,
        cred: creds.Credential | None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.profile = profile
        self.cred = cred
        # `transport` is an injection point for tests; production passes None.
        self._http = httpx.Client(
            timeout=_TIMEOUT, follow_redirects=False, transport=transport
        )

    # ─── plumbing ──────────────────────────────────────────────────────────

    def _require_cred(self) -> creds.Credential:
        if self.cred is None:
            raise TdtError(
                f"Not logged in for profile '{self.profile.name}'.",
                ExitCode.AUTH,
                hint="Run: tdt login",
            )
        return self.cred

    def _headers(self) -> dict[str, str]:
        cred = self._require_cred()
        h = {"Authorization": f"Bearer {cred.bearer}"}
        # An API key forces its own BU server-side; sending the header is
        # harmless but misleading, so only JWTs carry it.
        if cred.kind == "jwt" and self.profile.bu:
            h["X-Business-Unit"] = self.profile.bu
        return h

    def _maybe_refresh(self, force: bool = False) -> bool:
        """Redeem the refresh token. Returns True if a new access token landed."""
        cred = self.cred
        if cred is None or cred.kind != "jwt" or not cred.refresh_token:
            return False
        if not force and cred.access_token:
            remaining = creds.seconds_until_expiry(cred.access_token)
            # Refresh when the token is close to expiry OR unparseable. Only a
            # token with comfortable time left is a reason to skip — a missing
            # access token (the `--paste` flow stores just the refresh half) has
            # to redeem, not bail.
            if remaining is not None and remaining > _REFRESH_SKEW:
                return False
        try:
            resp = self._http.post(
                f"{self.profile.url}/auth/refresh",
                json={"refresh_token": cred.refresh_token},
            )
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        body = resp.json()
        self.cred = creds.Credential(
            kind="jwt",
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
        )
        creds.store(self.profile.name, self.cred)
        return True

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        allow_status: tuple[int, ...] = (),
    ) -> httpx.Response:
        self._maybe_refresh()
        url = f"{self.profile.url}{path}"

        def _send() -> httpx.Response:
            try:
                return self._http.request(
                    method, url, params=params, json=json_body, headers=self._headers()
                )
            except httpx.ConnectError as exc:
                raise TdtError(
                    f"Cannot reach {self.profile.url}",
                    ExitCode.API,
                    hint=(
                        "The API often resolves to an internal load balancer — "
                        "check you're on the VPN. This is a network failure, "
                        "not an auth one."
                    ),
                ) from exc
            except httpx.HTTPError as exc:
                raise TdtError(f"Transport error talking to TDT: {exc}", ExitCode.API) from exc

        resp = _send()
        if resp.status_code == 401 and self._maybe_refresh(force=True):
            resp = _send()

        if resp.status_code in allow_status:
            return resp
        if resp.status_code in (401, 403):
            cred = self._require_cred()
            detail = _detail(resp)
            # 401 and 403 are different problems and want different advice:
            # 401 = "we don't know who you are" (expired / revoked credential),
            # 403 = "we know, and you may not" (capability, role or BU scope).
            # Conflating them sends people to re-login over a permissions issue.
            if resp.status_code == 403:
                hint = (
                    "The API key's capability or workspace scope doesn't cover this "
                    "call (read < plan < apply < admin). Mint a key with a higher "
                    "capability, or use an interactive login."
                    if cred.kind == "api_key"
                    else "Your role or BU membership doesn't allow this. "
                    "Logging in again will not change it."
                )
                raise TdtError(f"Forbidden: {detail}", ExitCode.AUTH, hint=hint)
            if cred.kind == "api_key":
                raise TdtError(
                    f"TDT rejected the API key: {detail}",
                    ExitCode.AUTH,
                    hint="The key is revoked or expired — mint a new one in the UI.",
                )
            raise TdtError(
                f"Not authenticated: {detail}",
                ExitCode.AUTH,
                hint="Run: tdt login",
            )
        if resp.status_code >= 400:
            raise TdtError(
                f"{method} {path} → {resp.status_code}: {_detail(resp)}",
                ExitCode.API,
            )
        return resp

    # ─── verbs ─────────────────────────────────────────────────────────────

    def get(self, path: str, **kw) -> Any:
        return _body(self.request("GET", path, **kw))

    def post(self, path: str, json_body: Any = None, **kw) -> Any:
        return _body(self.request("POST", path, json_body=json_body, **kw))

    def put(self, path: str, json_body: Any = None, **kw) -> Any:
        return _body(self.request("PUT", path, json_body=json_body, **kw))

    def patch(self, path: str, json_body: Any = None, **kw) -> Any:
        return _body(self.request("PATCH", path, json_body=json_body, **kw))

    def delete(self, path: str, **kw) -> Any:
        return _body(self.request("DELETE", path, **kw))

    def close(self) -> None:
        self._http.close()


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:400] or "(empty response)"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in detail
        )
    return str(detail if detail is not None else body)[:400]


def _body(resp: httpx.Response) -> Any:
    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def auth_config(profile: Profile) -> dict:
    """`GET /auth/config` — which providers this deployment offers. Public, no token.

    Used by `tdt login` with no flags to choose between browser SSO and a
    password prompt instead of making the human know which one applies.
    """
    try:
        resp = httpx.get(f"{profile.url}/auth/config", timeout=_TIMEOUT)
    except httpx.ConnectError as exc:
        raise TdtError(
            f"Cannot reach {profile.url}",
            ExitCode.API,
            hint="Check the URL and whether you need the VPN.",
        ) from exc
    except httpx.HTTPError as exc:
        raise TdtError(f"Transport error talking to TDT: {exc}", ExitCode.API) from exc
    if resp.status_code != 200:
        # An older server without /auth/config still supports password login.
        return {"mode": "local", "oidc_enabled": False}
    return resp.json()


def login_password(profile: Profile, email: str, password: str) -> creds.Credential:
    """Exchange email+password for a JWT pair via `POST /auth/token`."""
    try:
        resp = httpx.post(
            f"{profile.url}/auth/token",
            json={"email": email, "password": password},
            timeout=_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        raise TdtError(
            f"Cannot reach {profile.url}",
            ExitCode.API,
            hint="Check the URL and whether you need the VPN.",
        ) from exc
    if resp.status_code == 401:
        raise TdtError("Invalid email or password.", ExitCode.AUTH)
    if resp.status_code != 200:
        raise TdtError(f"Login failed ({resp.status_code}): {_detail(resp)}", ExitCode.AUTH)
    body = resp.json()
    return creds.Credential(
        kind="jwt",
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
    )
