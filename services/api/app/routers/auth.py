"""Auth router: local password login + OIDC SSO (tested against a generic IdP).

Local login is the break-glass path and always available. OIDC is gated by the
`auth.provider` config key (`local` / `oidc` / `both`). See `app/auth/oidc.py`
for the config-key contract and `docs/claude/auth-oidc.md` for setup notes.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jose import JWTError

from app.db import get_db
from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.auth import oidc as oidc_mod
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.services import runtime_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Loopback hand-off nonce: url-safe base64 alphabet only. Anything else is a
# sign of tampering, and it lands in a redirect URL, so keep it inert.
_CLI_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")


async def _issue_pair(db: AsyncSession, user: User) -> TokenResponse:
    """Mint an access+refresh pair for `user` using the admin-configured TTLs.

    Claims are read from the **User row**, never copied from an incoming token,
    so a demotion (role change, superadmin revoked) takes effect on the next
    refresh instead of persisting for the life of the refresh token.
    """
    access_minutes = await runtime_settings.get_value(db, "auth.access_token_expire_minutes")
    refresh_hours = await runtime_settings.get_value(db, "auth.refresh_token_expire_hours")
    return TokenResponse(
        access_token=create_access_token(
            user.id, user.email, user.role,
            is_superadmin=bool(user.is_superadmin),
            name=user.display_name,
            expires_in_minutes=access_minutes,
        ),
        refresh_token=create_refresh_token(user.id, expires_in_hours=refresh_hours),
    )


# ─── Local password login (always available) ───────────────────────────────


@router.post("/token", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user and return JWT tokens.

    Note: even when OIDC is the primary provider, this endpoint stays live so
    a local admin (e.g. seeded `admin@test.com`) can sign in if the IdP is down.
    Disable by deleting all local users in prod, not by gating this endpoint.
    """
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalars().first()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    return await _issue_pair(db, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Redeem a refresh token for a fresh access+refresh pair.

    Both `/token` and the OIDC callback have always *minted* a refresh token;
    until this endpoint existed nothing could redeem one, which is why every
    non-browser caller had to either re-paste an hourly JWT or fall back to a
    long-lived `tdt_` API key. The CLI holds the refresh token and calls this
    when the access token nears expiry.

    **Sliding session:** a new refresh token is returned each time, so an
    actively-used session never has to re-authenticate. Tokens are stateless —
    there is no server-side revocation list — so a leaked refresh token stays
    usable until it expires (`auth.refresh_token_expire_hours`, default 24).
    Keep that TTL short-ish, and revoke access by deleting the user.

    Accepts refresh JWTs only: access tokens, run-scoped executor tokens and
    `tdt_` API keys are all rejected with 401.
    """
    try:
        payload = decode_token(body.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if payload.get("type") != "refresh":
        # An access or run token presented here must not be upgradeable into a
        # fresh long-lived pair.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a refresh token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user = await db.get(User, user_id)
    if user is None:
        # Deleting the user is the revocation mechanism for a stateless refresh.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return await _issue_pair(db, user)


# ─── Auth mode discovery (public) ──────────────────────────────────────────


@router.get("/config")
async def auth_config(db: AsyncSession = Depends(get_db)) -> dict:
    """Public endpoint the UI calls before rendering the login form.

    Tells the UI which providers are available so it can render a password
    form, an "Sign in with SSO" button, or both. Never exposes secrets.
    """
    mode = await oidc_mod.get_auth_mode(db)
    oidc_cfg = await oidc_mod.load_oidc_config(db) if mode != "local" else None
    return {
        "mode": mode,
        "oidc_enabled": oidc_cfg is not None,
        "oidc_issuer": oidc_cfg.issuer if oidc_cfg else None,
        # Capability marker for the `tdt` CLI. An older deployment simply omits
        # this key, which is exactly how the CLI detects that `--sso` would hang:
        # the old /oidc/login ignores cli_port and redirects the browser to the
        # SPA, so the CLI's loopback listener would wait for a callback that is
        # never sent. Never remove this key without bumping the CLI's check.
        "cli_loopback": True,
    }


# ─── OIDC: kickoff + callback ─────────────────────────────────────────────


def _stash_cli_handoff(
    session: dict, cli_port: int | None, cli_nonce: str | None
) -> None:
    """Record — or deliberately clear — the CLI loopback hand-off on the session.

    Clearing matters as much as setting: a plain browser login that inherited a
    stale `cli_port` from an abandoned `tdt login --sso` in the same session
    would otherwise be redirected to localhost instead of the app.
    """
    if cli_port is None:
        session.pop("cli_port", None)
        session.pop("cli_nonce", None)
        return
    if not cli_nonce or not _CLI_NONCE_RE.fullmatch(cli_nonce):
        raise HTTPException(
            status_code=400,
            detail="cli_nonce must accompany cli_port (16-128 url-safe chars)",
        )
    session["cli_port"] = int(cli_port)
    session["cli_nonce"] = cli_nonce


def _cli_handoff_response(port: int, nonce: str, pair: TokenResponse) -> HTMLResponse:
    """Bounce the browser to the CLI's loopback listener, then tell the human to close the tab.

    A `RedirectResponse` would put the tokens in the browser's address bar and
    session history. Instead this returns a tiny page that navigates with
    `location.replace()` — the loopback URL never becomes a history entry the
    user can navigate back to — and sends `Referrer-Policy: no-referrer` so the
    query string can't leak onward.

    **Known trade-off:** the token pair still travels through the loopback URL,
    so it is briefly visible to the local browser process. That is inherent to
    the loopback pattern; the hardening step, if it is ever wanted, is to hand
    over a one-time code and have the CLI POST it back for the real tokens
    (needs server-side code storage). Keep `auth.refresh_token_expire_hours`
    modest in the meantime.
    """
    target = (
        f"http://127.0.0.1:{port}/callback"
        f"?access_token={quote(pair.access_token)}"
        f"&refresh_token={quote(pair.refresh_token)}"
        f"&nonce={quote(nonce)}"
    )
    body = f"""<!doctype html>
<meta name="referrer" content="no-referrer">
<title>Terraducktel CLI sign-in</title>
<body style="font:14px system-ui;padding:3rem;text-align:center">
<p>Signing in to the <code>tdt</code> CLI…</p>
<p style="color:#666">You can close this tab once the terminal confirms.</p>
<script>location.replace({target!r});</script>
</body>"""
    return HTMLResponse(
        body,
        headers={
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.get("/oidc/login")
async def oidc_login(
    request: Request,
    cli_port: int | None = Query(
        None,
        ge=1024,
        le=65535,
        description=(
            "Loopback port the `tdt` CLI is listening on. When set, the callback "
            "hands the token pair back to http://127.0.0.1:<cli_port>/callback "
            "instead of the SPA. Requires `cli_nonce`."
        ),
    ),
    cli_nonce: str | None = Query(
        None,
        min_length=16,
        max_length=128,
        description="Random value the CLI generated; echoed back so it can "
                    "confirm the callback belongs to the login it started.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Begin the OIDC authorization-code flow. Redirects to the IdP.

    The IdP's registered `redirect_uri` never changes — it always points at this
    API's own `/oidc/callback`. The optional `cli_port` only changes where the
    callback sends the browser *afterwards*, and it travels in the signed session
    cookie rather than the callback URL, so a crafted callback can't retarget it.
    """
    mode = await oidc_mod.get_auth_mode(db)
    if mode == "local":
        raise HTTPException(status_code=404, detail="OIDC is not enabled")
    cfg = await oidc_mod.load_oidc_config(db)
    if cfg is None:
        raise HTTPException(status_code=503, detail="OIDC is not configured")

    _stash_cli_handoff(request.session, cli_port, cli_nonce)

    oauth = oidc_mod.build_oauth_client(cfg)
    # authlib stores the nonce + state in request.session — requires SessionMiddleware
    return await oauth.oidc.authorize_redirect(request, cfg.redirect_uri)


@router.get("/oidc/callback")
async def oidc_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange the auth code, mint a TDT JWT, redirect the browser back to UI.

    The UI reads `?access_token=…&refresh_token=…` from the fragment-free
    redirect URL and stows them via the existing login flow. For prod, this
    should be swapped to an `httpOnly` cookie set on the redirect response (see
    audit task #8 follow-up).
    """
    mode = await oidc_mod.get_auth_mode(db)
    if mode == "local":
        raise HTTPException(status_code=404, detail="OIDC is not enabled")
    cfg = await oidc_mod.load_oidc_config(db)
    if cfg is None:
        raise HTTPException(status_code=503, detail="OIDC is not configured")

    oauth = oidc_mod.build_oauth_client(cfg)
    try:
        token = await oauth.oidc.authorize_access_token(request)
    except Exception as exc:
        logger.warning("OIDC token exchange failed: %s", exc)
        raise HTTPException(status_code=401, detail="OIDC authentication failed")

    claims = dict(token.get("userinfo") or {})
    # If the role claim isn't in the id_token (some IdPs, in certain configs,
    # only emit `groups` from the /userinfo endpoint), fetch userinfo
    # explicitly and merge. Best-effort; if it fails we still proceed with
    # whatever's on the id_token.
    if cfg.role_claim not in claims:
        try:
            userinfo_resp = await oauth.oidc.userinfo(token=token)
            if userinfo_resp:
                # `userinfo_resp` is a dict-like UserInfo object; merge new
                # keys without overwriting anything the id_token already had.
                for k, v in dict(userinfo_resp).items():
                    claims.setdefault(k, v)
        except Exception as exc:
            logger.info("userinfo endpoint fetch failed: %s", exc)

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise HTTPException(status_code=401, detail="OIDC token missing required claims (sub/email)")

    # Diagnostic line: keys + the configured role claim's value, in a single
    # log entry. Keys (not values) of the whole claims dict so we don't spray
    # any PII, but explicitly include role_claim's raw value because that's
    # what the role-mapping operator needs to debug a "why am I viewer" bug.
    logger.info(
        "OIDC sign-in: sub=%s email=%s claim_keys=%s %s_raw=%r role_mapping=%r",
        sub, email, sorted(claims.keys()), cfg.role_claim,
        claims.get(cfg.role_claim), cfg.role_mapping,
    )

    role, is_superadmin = oidc_mod.role_for_user(claims, cfg)
    display_name = oidc_mod.display_name_from_claims(claims)
    logger.info(
        "OIDC role resolution: email=%s role=%s is_superadmin=%s display_name=%r",
        email, role, is_superadmin, display_name,
    )
    # `email_verified` gates linking to a pre-existing local account.
    # Treat the OIDC-standard truthy forms (bool True / "true") as verified.
    _ev = claims.get("email_verified")
    email_verified = _ev is True or (isinstance(_ev, str) and _ev.strip().lower() == "true")
    try:
        user = await oidc_mod.upsert_oidc_user(
            db, sub=sub, email=email, role=role,
            is_superadmin=is_superadmin, display_name=display_name,
            email_verified=email_verified,
        )
    except oidc_mod.UnverifiedEmailLinkError as exc:
        logger.warning("OIDC link refused for %s: %s", email, exc)
        raise HTTPException(status_code=403, detail=str(exc))
    await db.commit()

    pair = await _issue_pair(db, user)

    # CLI loopback hand-off (RFC 8252 native-app pattern). The host is hardcoded
    # to 127.0.0.1 — only the port comes from the session, so this can never be
    # turned into an open redirect to an external origin.
    cli_port = request.session.pop("cli_port", None)
    cli_nonce = request.session.pop("cli_nonce", None)
    if cli_port:
        return _cli_handoff_response(int(cli_port), str(cli_nonce or ""), pair)

    # Pass tokens back to the SPA via query string. Replace with an httpOnly
    # cookie when the cookie-auth migration lands (see task #8 follow-up).
    target = (
        f"/auth/oidc-finish?access_token={pair.access_token}"
        f"&refresh_token={pair.refresh_token}"
    )
    return RedirectResponse(target, status_code=302)
