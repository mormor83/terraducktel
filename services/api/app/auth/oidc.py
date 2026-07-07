"""OIDC provider for TerraDuckTel (tested against a generic OIDC-compatible IdP).

Design:
- Local password auth always works (break-glass admin). OIDC is additive.
- Config source = env vars first, config-table fallback. In ECS the task def
  reads each value from SSM Parameter Store (see secrets.tf / ecs.tf) and
  exposes them as the env vars below. Local docker-compose has none of these
  set so it stays on `local` auth with zero config.

    AUTH_PROVIDER              = "local" | "oidc" | "both"  (default: "local")
    AUTH_OIDC_ISSUER           = e.g. "https://your-idp.example.com/"
    AUTH_OIDC_CLIENT_ID        = OAuth application client ID
    AUTH_OIDC_CLIENT_SECRET    = secret  (SSM SecureString in prod)
    AUTH_OIDC_REDIRECT_URI     = e.g. "https://terraducktel.example.com/api/v1/auth/oidc/callback"
    AUTH_OIDC_SCOPES           = "openid email profile groups"  (default)
    AUTH_OIDC_ROLE_CLAIM       = id_token claim carrying groups (default "groups")
    AUTH_OIDC_ROLE_MAPPING     = JSON {"tdt-admins":"admin","tdt-ops":"operator",...}
    AUTH_OIDC_DEFAULT_ROLE     = role for users with no matching group (default "viewer")

The provider builds an authlib OAuth client on demand. Discovery is performed
once and cached (well-known/openid-configuration). User provisioning is JIT:
first time a user authenticates, we INSERT into `users` with auth_provider=oidc
and `external_id = sub`. Subsequent logins update role from group claims.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.user import User
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


PROVIDER_LOCAL = "local"
PROVIDER_OIDC = "oidc"
PROVIDER_BOTH = "both"

# Cache the openid-configuration document. The issuer rarely changes; a long
# TTL keeps the login flow snappy and survives short network blips.
_DISCOVERY_CACHE: dict[str, tuple[dict[str, Any], float]] = {}
_DISCOVERY_TTL_SECONDS = 60 * 60  # 1h


@dataclass
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    role_claim: str
    role_mapping: dict[str, str]
    default_role: str


async def _config(db: AsyncSession) -> ConfigService:
    return ConfigService(db, get_credential_encryption_key())


async def _env_or_cfg(db: AsyncSession, env_key: str, cfg_key: str) -> Optional[str]:
    """Env var wins. Empty string is treated as 'unset'.

    Lets ECS task definitions inject the SSM-backed values without us having
    to round-trip through the `config` table, while keeping local docker-
    compose dev working off the table (or just defaulting to `local`).
    """
    v = os.environ.get(env_key)
    if v is not None and v.strip():
        return v.strip()
    cs = await _config(db)
    return await cs.get(cfg_key)


async def get_auth_mode(db: AsyncSession) -> str:
    """Read the active auth mode. Defaults to `local` for safety."""
    val = (await _env_or_cfg(db, "AUTH_PROVIDER", "auth.provider")) or PROVIDER_LOCAL
    val = val.strip().lower()
    if val not in (PROVIDER_LOCAL, PROVIDER_OIDC, PROVIDER_BOTH):
        logger.warning("auth provider = %r is invalid; falling back to local", val)
        return PROVIDER_LOCAL
    return val


async def load_oidc_config(db: AsyncSession) -> Optional[OIDCConfig]:
    """Load OIDC config. Env vars (injected from SSM in ECS) take precedence
    over the config-table fallback used by local docker-compose dev. Returns
    None if any of the four mandatory fields are missing."""
    issuer = await _env_or_cfg(db, "AUTH_OIDC_ISSUER", "auth.oidc.issuer")
    client_id = await _env_or_cfg(db, "AUTH_OIDC_CLIENT_ID", "auth.oidc.client_id")
    client_secret = await _env_or_cfg(db, "AUTH_OIDC_CLIENT_SECRET", "auth.oidc.client_secret")
    redirect_uri = await _env_or_cfg(db, "AUTH_OIDC_REDIRECT_URI", "auth.oidc.redirect_uri")
    if not (issuer and client_id and client_secret and redirect_uri):
        return None
    scopes = (await _env_or_cfg(db, "AUTH_OIDC_SCOPES", "auth.oidc.scopes")) or "openid email profile groups"
    role_claim = (await _env_or_cfg(db, "AUTH_OIDC_ROLE_CLAIM", "auth.oidc.role_claim")) or "groups"
    default_role = (await _env_or_cfg(db, "AUTH_OIDC_DEFAULT_ROLE", "auth.oidc.default_role")) or "viewer"
    role_mapping_raw = await _env_or_cfg(db, "AUTH_OIDC_ROLE_MAPPING", "auth.oidc.role_mapping")
    try:
        role_mapping = json.loads(role_mapping_raw) if role_mapping_raw else {}
    except json.JSONDecodeError:
        logger.warning("AUTH_OIDC_ROLE_MAPPING is not valid JSON; ignoring")
        role_mapping = {}
    return OIDCConfig(
        issuer=issuer.rstrip("/") + "/",
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
        role_claim=role_claim,
        role_mapping=role_mapping,
        default_role=default_role,
    )


async def _discover(issuer: str) -> dict[str, Any]:
    """Fetch and cache the OIDC discovery document."""
    cached = _DISCOVERY_CACHE.get(issuer)
    if cached and cached[1] > time.time():
        return cached[0]
    url = issuer + ".well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    _DISCOVERY_CACHE[issuer] = (doc, time.time() + _DISCOVERY_TTL_SECONDS)
    return doc


def build_oauth_client(cfg: OIDCConfig) -> OAuth:
    """Construct an authlib OAuth registry pre-loaded with our `oidc` client.

    `token_endpoint_auth_method="client_secret_post"` matches the default
    custom-OIDC-app behavior of several common IdPs — accepting the
    client_secret in the POST body of the token-exchange request rather than
    the Authorization header. authlib defaults to `client_secret_basic`
    (header), which such providers reject with `invalid_client`. Set
    explicitly so most providers work out of the box, or fail loudly if not.
    """
    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        server_metadata_url=cfg.issuer + ".well-known/openid-configuration",
        client_kwargs={"scope": cfg.scopes},
        token_endpoint_auth_method="client_secret_post",
    )
    return oauth


def new_state_token() -> str:
    """Cryptographically random state token for the OAuth handshake."""
    return secrets.token_urlsafe(32)


# Permission targets the `role_mapping` may emit. `superadmin` is intentionally
# the highest-priority value — membership in *any* group that maps to it is
# enough to flip the cross-BU bypass flag. The other three drive the per-BU
# `role` column on the user row (legacy) and the membership-table role once
# the OIDC → user_business_units sync lands.
PRIORITY = {"superadmin": 4, "admin": 3, "operator": 2, "viewer": 1}


def role_for_user(claims: dict[str, Any], cfg: OIDCConfig) -> tuple[str, bool]:
    """Map id_token claims → (TDT role, is_superadmin).

    Read groups (or whichever claim `cfg.role_claim` names), match against
    `role_mapping`. Highest-priority match wins. A mapping value of
    `"superadmin"` flips the cross-BU bypass flag; the *role* field returned
    alongside is then "admin" (so the legacy `users.role` column reflects the
    user's effective privilege level even though `is_superadmin` is what the
    runtime checks consult).

    Falls back to `default_role` (and `is_superadmin=False`) if no group
    matches.

    Matching is **case-insensitive** on group names. Many IdPs emit groups
    in whatever case the operator typed (e.g. "DevOps") while admin-edited
    SSM mappings tend to be lowercase ("devops"). Comparing both halves
    after `.lower()` avoids a class of "why am I viewer" bugs that look
    indistinguishable from "groups claim missing" without the diagnostic log.
    """
    raw = claims.get(cfg.role_claim)
    if raw is None:
        return cfg.default_role, False
    groups = raw if isinstance(raw, list) else [raw]

    # Build a lowercase view of the role_mapping once so the per-group lookup
    # stays O(1).
    mapping_lower = {k.lower(): v for k, v in cfg.role_mapping.items()}

    best = (0, cfg.default_role)
    for g in groups:
        if not isinstance(g, str):
            continue
        mapped = mapping_lower.get(g.lower())
        if mapped and PRIORITY.get(mapped, 0) > best[0]:
            best = (PRIORITY[mapped], mapped)

    mapped = best[1]
    if mapped == "superadmin":
        # Store "admin" in the legacy role column for back-compat with any
        # code path that still reads `users.role`; the truth is `is_superadmin`.
        return "admin", True
    return mapped, False


def display_name_from_claims(claims: dict[str, Any]) -> Optional[str]:
    """Pick the best human-readable name from an OIDC id_token / userinfo.

    Order: explicit `name` claim → `given_name family_name` (if either present)
    → `preferred_username`. None of these are required by spec, so we may
    still return None — callers should fall back to the email local part.
    """
    name = claims.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    given = (claims.get("given_name") or "").strip() if isinstance(claims.get("given_name"), str) else ""
    family = (claims.get("family_name") or "").strip() if isinstance(claims.get("family_name"), str) else ""
    combined = " ".join(p for p in (given, family) if p)
    if combined:
        return combined
    pu = claims.get("preferred_username")
    if isinstance(pu, str) and pu.strip():
        return pu.strip()
    return None


class UnverifiedEmailLinkError(Exception):
    """Raised when an OIDC login would link to an existing account by an
    unverified email (account-takeover vector, )."""


async def upsert_oidc_user(
    db: AsyncSession,
    *,
    sub: str,
    email: str,
    role: str,
    is_superadmin: bool = False,
    display_name: Optional[str] = None,
    email_verified: bool = False,
) -> User:
    """JIT-provision (or update) an OIDC user. Returns the persisted row.

    `is_superadmin` is rewritten on every login — the IdP is the source of
    truth, so removing a user from the superadmin group on the IdP demotes
    them on next sign-in. Manual PATCH promotions on OIDC users will also be
    overridden by the next login; promote via group membership instead.

    Account linking safety: the `sub` claim is a stable, IdP-issued
    identifier and is always trusted. Falling back to match an EXISTING account
    by `email`, however, is only safe when the IdP asserts `email_verified` —
    otherwise an attacker who sets an arbitrary unverified email at their IdP
    could claim (and take over) another user's account, including a local
    break-glass admin. So we refuse to link to a pre-existing row on an
    unverified email; provisioning a brand-new account is still allowed (no
    existing account means nothing to take over).
    """
    # Match by external_id first (stable across email changes), fall back to email
    # for the very first login from a pre-existing local account being linked.
    result = await db.execute(select(User).where(User.external_id == sub))
    user = result.scalars().first()
    if user is None:
        result = await db.execute(select(User).where(User.email == email))
        candidate = result.scalars().first()
        if candidate is not None and not email_verified:
            raise UnverifiedEmailLinkError(
                "Refusing to link OIDC identity to an existing account by an "
                "unverified email. Set email_verified at your IdP, or have an "
                "admin link the account explicitly."
            )
        user = candidate

    if user is None:
        user = User(
            email=email,
            hashed_password="!OIDC!",  # never used; verify_password always fails
            role=role,
            is_superadmin=is_superadmin,
            auth_provider=PROVIDER_OIDC,
            external_id=sub,
            display_name=display_name,
        )
        db.add(user)
        await db.flush()
        logger.info(
            "Provisioned OIDC user %s with role=%s is_superadmin=%s",
            email, role, is_superadmin,
        )
        return user

    # Update mutable fields on every login. is_superadmin is rewritten from
    # the group claim — see the docstring; this is intentional so IdP changes
    # propagate without a manual sync step.
    user.email = email
    user.external_id = sub
    user.auth_provider = PROVIDER_OIDC
    user.role = role
    user.is_superadmin = is_superadmin
    # Refresh display_name from the IdP on every login so a renamed user
    # picks up the new label on next sign-in. None values don't overwrite
    # an existing display_name (preserves whatever was set last time if
    # the IdP suddenly stopped emitting the claim).
    if display_name:
        user.display_name = display_name
    await db.flush()
    return user
