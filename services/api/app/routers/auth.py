"""Auth router: local password login + OIDC SSO (tested against a generic IdP).

Local login is the break-glass path and always available. OIDC is gated by the
`auth.provider` config key (`local` / `oidc` / `both`). See `app/auth/oidc.py`
for the config-key contract and `docs/claude/auth-oidc.md` for setup notes.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.jwt import create_access_token, create_refresh_token, verify_password
from app.auth import oidc as oidc_mod
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services import runtime_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


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

    access_minutes = await runtime_settings.get_value(db, "auth.access_token_expire_minutes")
    refresh_hours = await runtime_settings.get_value(db, "auth.refresh_token_expire_hours")
    access_token = create_access_token(
        user.id, user.email, user.role,
        is_superadmin=bool(user.is_superadmin),
        name=user.display_name,
        expires_in_minutes=access_minutes,
    )
    refresh_token = create_refresh_token(user.id, expires_in_hours=refresh_hours)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


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
    }


# ─── OIDC: kickoff + callback ─────────────────────────────────────────────


@router.get("/oidc/login")
async def oidc_login(request: Request, db: AsyncSession = Depends(get_db)):
    """Begin the OIDC authorization-code flow. Redirects to the IdP."""
    mode = await oidc_mod.get_auth_mode(db)
    if mode == "local":
        raise HTTPException(status_code=404, detail="OIDC is not enabled")
    cfg = await oidc_mod.load_oidc_config(db)
    if cfg is None:
        raise HTTPException(status_code=503, detail="OIDC is not configured")

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

    access_minutes = await runtime_settings.get_value(db, "auth.access_token_expire_minutes")
    refresh_hours = await runtime_settings.get_value(db, "auth.refresh_token_expire_hours")
    access_token = create_access_token(
        user.id, user.email, user.role,
        is_superadmin=bool(user.is_superadmin),
        name=user.display_name,
        expires_in_minutes=access_minutes,
    )
    refresh_token = create_refresh_token(user.id, expires_in_hours=refresh_hours)

    # Pass tokens back to the SPA via query string. Replace with an httpOnly
    # cookie when the cookie-auth migration lands (see task #8 follow-up).
    target = f"/auth/oidc-finish?access_token={access_token}&refresh_token={refresh_token}"
    return RedirectResponse(target, status_code=302)
