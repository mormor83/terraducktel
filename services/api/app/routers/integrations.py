"""Third-party integration credentials.

Stored in the encrypted Config table (`is_secret=True`) so the same Fernet/HKDF
scheme that protects AWS credentials also protects Slack / GitHub / etc. The
plaintext value is NEVER returned in any response — the GET endpoint exposes
only `configured: bool` and a masked tail.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.encryption_key import get_credential_encryption_key
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.changelog_entry import ChangelogEntry as ChangelogEntryModel
from app.models.config import Config
from app.models.user import User
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

GITHUB_TOKEN_KEY = "github.token"
GITHUB_ORG_KEY = "github.org"
WEBHOOK_SECRET_KEY = "webhook.secret"
MODULES_CONFIG_KEY = "modules.config"
INFRA_REPO_KEY = "infra.repo_url"
CHANGELOG_REPO_KEY = "changelog.repo"
CHECKOV_MODE_KEY = "checkov.mode"
# OPA/conftest policy gate (Settings → Policies). All per-BU via bu.<slug>.*.
OPA_MODE_KEY = "opa.mode"
OPA_USE_BUNDLED_KEY = "opa.use_bundled"
OPA_BUNDLED_SEVERITY_KEY = "opa.bundled_severity"
OPA_GIT_SEVERITY_KEY = "opa.git_severity"
OPA_REPO_URL_KEY = "opa.repo_url"
OPA_REPO_REF_KEY = "opa.repo_ref"
OPA_REPO_DIR_KEY = "opa.repo_dir"
INFRACOST_API_KEY_KEY = "infracost.api_key"
INFRACOST_CURRENCY_KEY = "infracost.currency"
SLACK_BOT_TOKEN_KEY = "slack.bot_token"
SLACK_CHANNEL_ID_KEY = "slack.channel_id"
SLACK_CHANNEL_NAME_KEY = "slack.channel_name"
SLACK_TEAM_NAME_KEY = "slack.team_name"


# ─── schemas ────────────────────────────────────────────────────────────────


class GitHubTokenResponse(BaseModel):
    configured: bool
    token_tail: Optional[str] = None
    # If env-var GITHUB_TOKEN is set, the executor uses that instead and the
    # DB row is irrelevant. Surface it so admins aren't confused.
    overridden_by_env: bool = False
    # True when no BU-scoped key is set and we're returning the legacy global
    # `github.token` value. UI shows an amber "inherited" hint so admins know
    # this BU hasn't saved its own value yet — the next PUT will write
    # bu.<slug>.github.token and `inherited` flips to false.
    inherited: bool = False


class GitHubTokenSet(BaseModel):
    token: str = Field(..., min_length=8)


class GitHubTokenTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    login: Optional[str] = None
    scopes: Optional[list[str]] = None


class WebhookConfigResponse(BaseModel):
    """Per-BU webhook config (no secret returned — masked tail only).

    The BU webhook URL is `${API}/api/v1/webhooks/github/${slug}`.
    """
    bu_slug: str
    configured: bool
    secret_tail: Optional[str] = None
    github_org: Optional[str] = None
    webhook_path: str


class WebhookConfigSet(BaseModel):
    secret: Optional[str] = Field(default=None, min_length=8)
    github_org: Optional[str] = Field(default=None, max_length=128)


class InfracostStatus(BaseModel):
    configured: bool
    api_key_tail: Optional[str] = None
    currency: str = "USD"
    overridden_by_env: bool = False
    inherited: bool = False


class InfracostUpdate(BaseModel):
    api_key: Optional[str] = None  # set empty string to remove
    currency: Optional[str] = Field(default=None, max_length=8)


class InfracostTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    organization: Optional[str] = None


class CheckovModeConfig(BaseModel):
    """How the executor reacts to Checkov security findings.

    - `fail` (default): any finding aborts the run before terraform plan.
    - `warn`: findings are captured as the step's output but the run continues
      so you can iterate on a brownfield codebase without rewriting modules.
    """
    mode: str = Field("fail", pattern=r"^(fail|warn)$")
    # True when no BU-scoped key is set and we're returning the legacy global
    # `checkov.mode` value.
    inherited: bool = False


class OpaConfig(BaseModel):
    """OPA/conftest policy gate behavior (per BU).

    `mode` is the master switch:
      - `off` (default): the executor's OPA Policy Check step is skipped.
      - `warn`: policies run and findings are recorded, but nothing blocks.
      - `enforce`: a `block`-severity policy violation fails the run before
        approval; `warn`/`info` severity stays advisory.

    `use_bundled` includes the executor image's built-in defaults
    (`/opt/tdt/policies/bundled`). An optional git policy-repo
    (`repo_url`/`repo_ref`/`repo_dir`) is merged in too. Bundled + git policies
    have no per-rule severity, so they take `bundled_severity` / `git_severity`.
    """
    mode: str = Field("off", pattern=r"^(enforce|warn|off)$")
    use_bundled: bool = True
    bundled_severity: str = Field("block", pattern=r"^(block|warn|info)$")
    git_severity: str = Field("block", pattern=r"^(block|warn|info)$")
    repo_url: str = ""
    repo_ref: str = "main"
    repo_dir: str = ""
    # True when no BU-scoped row exists yet and we're returning defaults/legacy.
    inherited: bool = False


class ModulesConfig(BaseModel):
    """Where to fetch terraform modules referenced by `module "x" { source = ... }`.

    `github` mode: clone the upstream URL via the configured GitHub token.
    `local` mode: bind-mount `local_host_dir` into the executor and rewrite the
      upstream URL to `file://` so the same `source = ...` lines resolve to a
      developer-mounted checkout instead.
    """
    mode: str = Field(..., pattern=r"^(github|local)$")
    upstream_url: str = ""
    local_host_dir: str = ""
    inherited: bool = False


class InfraRepoConfig(BaseModel):
    """Default base-infrastructure repo URL used to prefill the Git import
    (workspace discovery) form. Not a secret — it's just a convenience default
    so operators don't retype the same URL on every discover."""
    repo_url: str = ""
    inherited: bool = False


class ChangelogConfig(BaseModel):
    """Which GitHub repo (`owner/repo`) the Settings → Changelog tab reads
    Releases from. The TDT product repo by convention."""
    repo: str = ""
    configured: bool = False
    inherited: bool = False


class ChangelogEntryOut(BaseModel):
    """A stored changelog row (read from TDT's DB, not GitHub).

    `source` is "github" (synced from a merged PR) or "manual" (admin-authored).
    `ref` is the PR number for github rows, null for manual."""
    id: str
    source: str
    ref: Optional[str] = None
    title: str
    body: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    entry_date: Optional[str] = None


class ChangelogEntryCreate(BaseModel):
    """Payload for a manual changelog entry."""
    title: str = Field(..., min_length=1, max_length=300)
    body: Optional[str] = None
    url: Optional[str] = None
    # ISO8601; defaults to "now" server-side when omitted.
    entry_date: Optional[str] = None


class ChangelogSyncResult(BaseModel):
    synced: int
    total: int


class _PrEntry(BaseModel):
    """Internal: one merged PR parsed from the GitHub API."""
    number: int
    title: str
    merged_at: Optional[str] = None
    html_url: Optional[str] = None
    author: Optional[str] = None
    body: Optional[str] = None


# ─── helpers ────────────────────────────────────────────────────────────────


def _config_svc(db: AsyncSession) -> ConfigService:
    return ConfigService(db, get_credential_encryption_key())


def _mask_tail(token: str) -> str:
    if not token:
        return ""
    return f"…{token[-4:]}" if len(token) > 4 else "****"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO8601 timestamp (tolerating a trailing 'Z'); None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ─── routes ─────────────────────────────────────────────────────────────────


def _require_bu(bu: BUScope) -> str:
    if bu.slug is None:
        raise HTTPException(
            status_code=400,
            detail="Set X-Business-Unit header to a specific BU",
        )
    return bu.slug


@router.get("/github", response_model=GitHubTokenResponse)
async def get_github_token(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    # Distinguish BU-scoped value vs. legacy global fallback so the UI can
    # render "inherited" instead of a misleading "configured" badge for a
    # freshly created BU that hasn't saved its own PAT yet.
    bu_token = await svc.get(svc.bu_key(slug, GITHUB_TOKEN_KEY))
    global_token = await svc.get(GITHUB_TOKEN_KEY) if bu_token is None else None
    token = bu_token if bu_token is not None else global_token
    env_override = bool(os.environ.get("GITHUB_TOKEN", "").strip())
    if not token:
        return GitHubTokenResponse(
            configured=False, overridden_by_env=env_override,
        )
    return GitHubTokenResponse(
        configured=True,
        token_tail=_mask_tail(token),
        overridden_by_env=env_override,
        inherited=(bu_token is None),
    )


@router.put("/github", response_model=GitHubTokenResponse)
async def set_github_token(
    body: GitHubTokenSet,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    await svc.set_for_bu(
        slug,
        GITHUB_TOKEN_KEY,
        body.token,
        is_secret=True,
        description=f"GitHub PAT for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await db.commit()
    return GitHubTokenResponse(
        configured=True,
        token_tail=_mask_tail(body.token),
        overridden_by_env=bool(os.environ.get("GITHUB_TOKEN", "").strip()),
    )


@router.delete("/github", status_code=204)
async def delete_github_token(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    await svc.delete_for_bu(slug, GITHUB_TOKEN_KEY)
    await db.commit()


@router.post("/github/test", response_model=GitHubTokenTestResult)
async def test_github_token(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Probe the configured token against GitHub: hit /user, surface login + scopes."""
    slug = _require_bu(bu)
    svc = _config_svc(db)
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or (await svc.get_for_bu(slug, GITHUB_TOKEN_KEY) or "").strip())
    if not token:
        raise HTTPException(status_code=400, detail="No GitHub token configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "terraducktel",
                },
            )
        if r.status_code == 200:
            scopes_hdr = r.headers.get("X-OAuth-Scopes", "")
            scopes = [s.strip() for s in scopes_hdr.split(",") if s.strip()]
            return GitHubTokenTestResult(
                ok=True, login=r.json().get("login"), scopes=scopes,
            )
        return GitHubTokenTestResult(
            ok=False,
            detail=f"GitHub returned {r.status_code}: {r.text[:160]}",
        )
    except httpx.RequestError as e:
        return GitHubTokenTestResult(ok=False, detail=f"Network error: {e!s}")


# ─── Webhook (per-BU secret + org binding) ─────────────────────────────────


@router.get("/webhook", response_model=WebhookConfigResponse)
async def get_webhook_config(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    secret = (await svc.get_for_bu(slug, WEBHOOK_SECRET_KEY) or "").strip()
    org = (await svc.get_for_bu(slug, GITHUB_ORG_KEY) or "").strip()
    return WebhookConfigResponse(
        bu_slug=slug,
        configured=bool(secret),
        secret_tail=_mask_tail(secret) if secret else None,
        github_org=org or None,
        webhook_path=f"/api/v1/webhooks/github/{slug}",
    )


@router.put("/webhook", response_model=WebhookConfigResponse)
async def set_webhook_config(
    body: WebhookConfigSet,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    if body.secret is not None:
        await svc.set_for_bu(
            slug,
            WEBHOOK_SECRET_KEY,
            body.secret,
            is_secret=True,
            description=f"GitHub webhook HMAC secret for BU '{slug}'.",
            updated_by=current_user.id,
        )
    if body.github_org is not None:
        await svc.set_for_bu(
            slug,
            GITHUB_ORG_KEY,
            body.github_org.strip(),
            is_secret=False,
            description=f"GitHub org login for BU '{slug}'.",
            updated_by=current_user.id,
        )
    await db.commit()
    return await get_webhook_config(current_user, bu, db)


# ─── Infracost (cost estimation) ────────────────────────────────────────────


@router.get("/infracost", response_model=InfracostStatus)
async def get_infracost(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    # BU-scoped first, with explicit fallback to the legacy global keys so we
    # can flag `inherited` for the UI.
    bu_key = await svc.get(svc.bu_key(slug, INFRACOST_API_KEY_KEY))
    bu_currency = await svc.get(svc.bu_key(slug, INFRACOST_CURRENCY_KEY))
    if bu_key is None:
        key = (await svc.get(INFRACOST_API_KEY_KEY)) or ""
    else:
        key = bu_key
    currency = (bu_currency or await svc.get(INFRACOST_CURRENCY_KEY) or "USD").strip()
    env_override = bool(os.environ.get("INFRACOST_API_KEY", "").strip())
    return InfracostStatus(
        configured=bool(key),
        api_key_tail=_mask_tail(key) if key else None,
        currency=currency or "USD",
        overridden_by_env=env_override,
        inherited=(bu_key is None and bool(key)),
    )


@router.put("/infracost", response_model=InfracostStatus)
async def set_infracost(
    body: InfracostUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    if body.api_key is not None:
        if body.api_key.strip() == "":
            await svc.delete_for_bu(slug, INFRACOST_API_KEY_KEY)
        else:
            await svc.set_for_bu(
                slug,
                INFRACOST_API_KEY_KEY,
                body.api_key.strip(),
                is_secret=True,
                description=f"Infracost API key for BU '{slug}'.",
                updated_by=current_user.id,
            )
    if body.currency is not None and body.currency.strip():
        await svc.set_for_bu(
            slug,
            INFRACOST_CURRENCY_KEY,
            body.currency.strip().upper(),
            is_secret=False,
            description=f"Infracost currency code for BU '{slug}'.",
            updated_by=current_user.id,
        )
    await db.commit()
    return await get_infracost(current_user, bu, db)


@router.post("/infracost/test", response_model=InfracostTestResult)
async def test_infracost(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    key = (os.environ.get("INFRACOST_API_KEY", "").strip()
           or (await svc.get_for_bu(slug, INFRACOST_API_KEY_KEY) or "").strip())
    if not key:
        raise HTTPException(status_code=400, detail="No Infracost API key configured")
    # Infracost has no dedicated /auth/me endpoint. The CLI validates a key by
    # sending a trivial GraphQL ping against the pricing endpoint — a valid key
    # returns 200 with {"data": {"__typename": "Query"}}; an invalid key
    # returns 401/403 or a payload with `errors`.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://pricing.api.infracost.io/graphql",
                headers={
                    "X-Api-Key": key,
                    "User-Agent": "terraducktel",
                    "Content-Type": "application/json",
                },
                json={"query": "{ __typename }"},
            )
        if r.status_code == 200:
            try:
                payload = r.json() or {}
            except ValueError:
                payload = {}
            errs = payload.get("errors") or []
            if errs:
                msg = errs[0].get("message") if isinstance(errs[0], dict) else str(errs[0])
                return InfracostTestResult(ok=False, detail=str(msg)[:200])
            return InfracostTestResult(ok=True, organization=None)
        if r.status_code in (401, 403):
            return InfracostTestResult(ok=False, detail="Invalid Infracost API key")
        return InfracostTestResult(
            ok=False,
            detail=f"Infracost returned {r.status_code}: {r.text[:160]}",
        )
    except httpx.RequestError as e:
        return InfracostTestResult(ok=False, detail=f"Network error: {e!s}")


# ─── Checkov security gate mode ─────────────────────────────────────────────


@router.get("/checkov", response_model=CheckovModeConfig)
async def get_checkov_mode(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    bu_raw = await svc.get(svc.bu_key(slug, CHECKOV_MODE_KEY))
    raw = bu_raw if bu_raw is not None else (await svc.get(CHECKOV_MODE_KEY) or "")
    raw = (raw or "").strip()
    if raw not in ("fail", "warn"):
        raw = "fail"
    return CheckovModeConfig(mode=raw, inherited=(bu_raw is None))


@router.put("/checkov", response_model=CheckovModeConfig)
async def set_checkov_mode(
    body: CheckovModeConfig,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    await svc.set_for_bu(
        slug,
        CHECKOV_MODE_KEY,
        body.mode,
        is_secret=False,
        description=f"Checkov gate mode for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await db.commit()
    # `inherited` is always False after a successful save — we just wrote
    # this BU's own row.
    return CheckovModeConfig(mode=body.mode, inherited=False)


# ─── OPA / conftest policy gate ─────────────────────────────────────────────


def _as_bool(raw: Optional[str], default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _norm_sev(raw: Optional[str], default: str = "block") -> str:
    val = (raw or "").strip()
    return val if val in ("block", "warn", "info") else default


@router.get("/opa", response_model=OpaConfig)
async def get_opa_config(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    bu_mode = await svc.get(svc.bu_key(slug, OPA_MODE_KEY))
    mode = (bu_mode if bu_mode is not None else (await svc.get(OPA_MODE_KEY) or "")).strip()
    if mode not in ("enforce", "warn", "off"):
        mode = "off"
    return OpaConfig(
        mode=mode,
        use_bundled=_as_bool(await svc.get_for_bu(slug, OPA_USE_BUNDLED_KEY), True),
        bundled_severity=_norm_sev(await svc.get_for_bu(slug, OPA_BUNDLED_SEVERITY_KEY)),
        git_severity=_norm_sev(await svc.get_for_bu(slug, OPA_GIT_SEVERITY_KEY)),
        repo_url=(await svc.get_for_bu(slug, OPA_REPO_URL_KEY) or "").strip(),
        repo_ref=(await svc.get_for_bu(slug, OPA_REPO_REF_KEY) or "main").strip(),
        repo_dir=(await svc.get_for_bu(slug, OPA_REPO_DIR_KEY) or "").strip(),
        inherited=(bu_mode is None),
    )


@router.put("/opa", response_model=OpaConfig)
async def set_opa_config(
    body: OpaConfig,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    pairs = {
        OPA_MODE_KEY: body.mode,
        OPA_USE_BUNDLED_KEY: "true" if body.use_bundled else "false",
        OPA_BUNDLED_SEVERITY_KEY: body.bundled_severity,
        OPA_GIT_SEVERITY_KEY: body.git_severity,
        OPA_REPO_URL_KEY: body.repo_url.strip(),
        OPA_REPO_REF_KEY: (body.repo_ref or "main").strip(),
        OPA_REPO_DIR_KEY: body.repo_dir.strip(),
    }
    for key, value in pairs.items():
        await svc.set_for_bu(
            slug, key, value, is_secret=False,
            description=f"OPA policy gate ({key}) for BU '{slug}'.",
            updated_by=current_user.id,
        )
    await db.commit()
    return OpaConfig(**body.model_dump(exclude={"inherited"}), inherited=False)


# ─── Terraform modules registry ─────────────────────────────────────────────


@router.get("/modules", response_model=ModulesConfig)
async def get_modules_config(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Return the BU's saved modules-registry config or a sensible default.

    Falls back to the legacy global `modules.config` key for one release so
    existing deployments keep working until they re-save under the BU.
    """
    import json as _json

    slug = _require_bu(bu)
    svc = _config_svc(db)
    bu_raw = await svc.get(svc.bu_key(slug, MODULES_CONFIG_KEY))
    raw = bu_raw if bu_raw is not None else await svc.get(MODULES_CONFIG_KEY)
    if not raw:
        return ModulesConfig(mode="github", upstream_url="", local_host_dir="")
    try:
        data = _json.loads(raw)
    except Exception:
        return ModulesConfig(mode="github", upstream_url="", local_host_dir="")
    return ModulesConfig(
        mode=data.get("mode", "github"),
        upstream_url=data.get("upstream_url", ""),
        local_host_dir=data.get("local_host_dir", ""),
        inherited=(bu_raw is None),
    )


@router.put("/modules", response_model=ModulesConfig)
async def set_modules_config(
    body: ModulesConfig,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    import json as _json

    if body.mode == "local" and not body.local_host_dir:
        raise HTTPException(
            status_code=422, detail="local mode requires a non-empty local_host_dir",
        )
    slug = _require_bu(bu)
    svc = _config_svc(db)
    await svc.set_for_bu(
        slug,
        MODULES_CONFIG_KEY,
        _json.dumps(body.model_dump()),
        is_secret=False,
        description=f"Terraform modules registry for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await db.commit()
    return body


# ─── Default infra repo (Git import prefill) ───────────────────────────────


@router.get("/infra-repo", response_model=InfraRepoConfig)
async def get_infra_repo(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Return the BU's default base-infra repo URL (or legacy-global fallback).

    Used by the UI to prefill the Git-import Discover form so operators don't
    retype the same URL each time.
    """
    slug = _require_bu(bu)
    svc = _config_svc(db)
    bu_raw = await svc.get(svc.bu_key(slug, INFRA_REPO_KEY))
    raw = bu_raw if bu_raw is not None else await svc.get(INFRA_REPO_KEY)
    return InfraRepoConfig(repo_url=raw or "", inherited=(bu_raw is None and bool(raw)))


@router.put("/infra-repo", response_model=InfraRepoConfig)
async def set_infra_repo(
    body: InfraRepoConfig,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    await svc.set_for_bu(
        slug,
        INFRA_REPO_KEY,
        body.repo_url.strip(),
        is_secret=False,
        description=f"Default base-infra repo URL for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await db.commit()
    return InfraRepoConfig(repo_url=body.repo_url.strip())


# ─── Changelog (GitHub Releases of the TDT repo) ───────────────────────────


@router.get("/changelog", response_model=ChangelogConfig)
async def get_changelog_config(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    bu_raw = await svc.get(svc.bu_key(slug, CHANGELOG_REPO_KEY))
    raw = bu_raw if bu_raw is not None else await svc.get(CHANGELOG_REPO_KEY)
    repo = (raw or "").strip()
    return ChangelogConfig(
        repo=repo,
        configured=bool(repo),
        inherited=(bu_raw is None and bool(repo)),
    )


@router.put("/changelog", response_model=ChangelogConfig)
async def set_changelog_config(
    body: ChangelogConfig,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    repo = body.repo.strip()
    # Light shape check — "owner/repo", no scheme/extra path segments.
    if repo and (repo.count("/") != 1 or repo.startswith("/") or repo.endswith("/")):
        raise HTTPException(
            status_code=422,
            detail="repo must be in 'owner/repo' form (e.g. your-org/terraducktel)",
        )
    slug = _require_bu(bu)
    svc = _config_svc(db)
    await svc.set_for_bu(
        slug,
        CHANGELOG_REPO_KEY,
        repo,
        is_secret=False,
        description=f"Changelog source repo for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await db.commit()
    return ChangelogConfig(repo=repo, configured=bool(repo))


async def _fetch_github_prs(repo: str, token: str) -> list[_PrEntry]:
    """Fetch merged PRs for `repo` from GitHub (newest first, up to 30).

    The project ships via merged PRs (deploy-on-dev-push) rather than tagged
    Releases, so PR titles + merge dates are the real changelog. `token` is
    optional (public repos resolve tokenless); it's never returned to callers.
    Raises HTTPException(502) on any GitHub-side failure — including a 404,
    which for a private repo means "token can't see it", NOT "not configured".
    """
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "terraducktel"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://api.github.com/repos/{repo}/pulls",
                params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 50},
                headers=headers,
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"GitHub network error: {e!s}")
    if r.status_code == 404:
        raise HTTPException(
            status_code=502,
            detail=(
                f"GitHub couldn't access '{repo}'. If it's private, add a GitHub "
                f"token with repo access in Settings → GitHub; otherwise check the owner/repo."
            ),
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub returned {r.status_code}: {r.text[:160]}")
    merged = [pr for pr in r.json() if pr.get("merged_at")]
    merged.sort(key=lambda pr: pr.get("merged_at") or "", reverse=True)
    return [
        _PrEntry(
            number=pr.get("number"),
            title=pr.get("title") or "",
            merged_at=pr.get("merged_at"),
            html_url=pr.get("html_url"),
            author=(pr.get("user") or {}).get("login"),
            body=pr.get("body"),
        )
        for pr in merged[:30]
    ]


def _entry_out(row: ChangelogEntryModel) -> ChangelogEntryOut:
    return ChangelogEntryOut(
        id=row.id,
        source=row.source,
        ref=row.ref,
        title=row.title,
        body=row.body,
        author=row.author,
        url=row.url,
        entry_date=row.entry_date.isoformat() if row.entry_date else None,
    )


@router.get("/changelog/entries", response_model=list[ChangelogEntryOut])
async def list_changelog_entries(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Return stored changelog entries for the current BU (newest first).

    Reads only from TDT's DB — never hits GitHub. Populate `github` rows with
    POST /changelog/sync; add `manual` rows with POST /changelog/entries.
    """
    slug = _require_bu(bu)
    res = await db.execute(
        select(ChangelogEntryModel)
        .where(ChangelogEntryModel.business_unit_id == slug)
        .order_by(
            ChangelogEntryModel.entry_date.desc().nullslast(),
            ChangelogEntryModel.created_at.desc(),
        )
    )
    return [_entry_out(row) for row in res.scalars().all()]


@router.post("/changelog/sync", response_model=ChangelogSyncResult)
async def sync_changelog(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Pull merged PRs from the configured repo into TDT, upserting by PR number.

    404 → no repo configured; 502 → GitHub-side failure (surfaced verbatim).
    """
    slug = _require_bu(bu)
    svc = _config_svc(db)
    repo = (await svc.get_for_bu(slug, CHANGELOG_REPO_KEY) or "").strip()
    if not repo:
        raise HTTPException(
            status_code=404,
            detail="No changelog repo configured. Set one in Settings → Changelog.",
        )
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or (await svc.get_for_bu(slug, GITHUB_TOKEN_KEY) or "").strip())
    prs = await _fetch_github_prs(repo, token)

    # Index existing github rows for this BU by PR number → update in place.
    existing = {
        row.ref: row
        for row in (
            await db.execute(
                select(ChangelogEntryModel).where(
                    ChangelogEntryModel.business_unit_id == slug,
                    ChangelogEntryModel.source == "github",
                )
            )
        ).scalars().all()
    }
    for pr in prs:
        ref = str(pr.number)
        merged_dt = _parse_iso(pr.merged_at)
        row = existing.get(ref)
        if row is None:
            db.add(
                ChangelogEntryModel(
                    business_unit_id=slug,
                    source="github",
                    ref=ref,
                    title=pr.title,
                    body=pr.body,
                    author=pr.author,
                    url=pr.html_url,
                    entry_date=merged_dt,
                )
            )
        else:
            row.title = pr.title
            row.body = pr.body
            row.author = pr.author
            row.url = pr.html_url
            row.entry_date = merged_dt
    await db.commit()
    return ChangelogSyncResult(synced=len(prs), total=len(prs))


@router.post("/changelog/entries", response_model=ChangelogEntryOut, status_code=201)
async def create_changelog_entry(
    body: ChangelogEntryCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Add a manual changelog entry for the current BU."""
    slug = _require_bu(bu)
    entry_dt = _parse_iso(body.entry_date) or datetime.now(timezone.utc)
    row = ChangelogEntryModel(
        business_unit_id=slug,
        source="manual",
        ref=None,
        title=body.title.strip(),
        body=(body.body or None),
        author=current_user.email,
        url=(body.url or None),
        entry_date=entry_dt,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _entry_out(row)


@router.delete("/changelog/entries/{entry_id}", status_code=204)
async def delete_changelog_entry(
    entry_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Delete one changelog entry (manual or synced) from the current BU."""
    slug = _require_bu(bu)
    res = await db.execute(
        sa_delete(ChangelogEntryModel).where(
            ChangelogEntryModel.id == entry_id,
            ChangelogEntryModel.business_unit_id == slug,
        )
    )
    await db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")


# ─── Slack (per-BU bot token + channel) ────────────────────────────────────


class SlackStatus(BaseModel):
    """GET response: never returns the bot token. UI sees only a tail + the
    cached team name so admins can confirm "this is the right workspace"."""
    configured: bool
    token_tail: Optional[str] = None
    team_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None


class SlackUpdate(BaseModel):
    """PUT payload. `token` is required on first save; on subsequent saves
    callers can omit it to keep the existing one and just update the
    channel selection."""
    token: Optional[str] = Field(default=None, min_length=8)
    channel_id: Optional[str] = Field(default=None, max_length=64)
    channel_name: Optional[str] = Field(default=None, max_length=128)


class SlackTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    team: Optional[str] = None
    bot_user_id: Optional[str] = None
    url: Optional[str] = None


class SlackChannelOut(BaseModel):
    id: str
    name: str
    is_private: bool = False


@router.get("/slack", response_model=SlackStatus)
async def get_slack_status(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    token = await svc.get_for_bu(slug, SLACK_BOT_TOKEN_KEY)
    if not token:
        return SlackStatus(configured=False)
    return SlackStatus(
        configured=True,
        token_tail=_mask_tail(token),
        team_name=await svc.get_for_bu(slug, SLACK_TEAM_NAME_KEY),
        channel_id=await svc.get_for_bu(slug, SLACK_CHANNEL_ID_KEY),
        channel_name=await svc.get_for_bu(slug, SLACK_CHANNEL_NAME_KEY),
    )


@router.put("/slack", response_model=SlackStatus)
async def set_slack_config(
    body: SlackUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Save Slack config. Requires the bot token to be valid on first save;
    we call `auth.test` synchronously and cache the team name so the UI
    can confirm the connection without re-hitting Slack."""
    from app.services import slack as slack_svc

    slug = _require_bu(bu)
    svc = _config_svc(db)

    existing_token = await svc.get_for_bu(slug, SLACK_BOT_TOKEN_KEY)
    token = (body.token or "").strip() or existing_token
    if not token:
        raise HTTPException(
            status_code=422,
            detail="Bot token is required on first save",
        )

    # Verify the token *now* — saving a broken token would mean every later
    # notification fails silently in the worker.
    try:
        identity = await slack_svc.verify_token(token)
    except slack_svc.SlackError as e:
        raise HTTPException(
            status_code=400, detail=f"Slack rejected token: {e.code}"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Slack unreachable: {e!s}")

    await svc.set_for_bu(
        slug,
        SLACK_BOT_TOKEN_KEY,
        token,
        is_secret=True,
        description=f"Slack bot token for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await svc.set_for_bu(
        slug,
        SLACK_TEAM_NAME_KEY,
        identity.team,
        is_secret=False,
        description=f"Slack workspace name (cached) for BU '{slug}'.",
        updated_by=current_user.id,
    )
    if body.channel_id is not None:
        await svc.set_for_bu(
            slug, SLACK_CHANNEL_ID_KEY, body.channel_id,
            is_secret=False,
            description=f"Slack channel id for BU '{slug}'.",
            updated_by=current_user.id,
        )
    if body.channel_name is not None:
        await svc.set_for_bu(
            slug, SLACK_CHANNEL_NAME_KEY, body.channel_name,
            is_secret=False,
            description=f"Slack channel name for BU '{slug}'.",
            updated_by=current_user.id,
        )
    await db.commit()

    return SlackStatus(
        configured=True,
        token_tail=_mask_tail(token),
        team_name=identity.team,
        channel_id=body.channel_id or await svc.get_for_bu(slug, SLACK_CHANNEL_ID_KEY),
        channel_name=body.channel_name or await svc.get_for_bu(slug, SLACK_CHANNEL_NAME_KEY),
    )


@router.delete("/slack", status_code=204)
async def delete_slack_config(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    for k in (
        SLACK_BOT_TOKEN_KEY,
        SLACK_TEAM_NAME_KEY,
        SLACK_CHANNEL_ID_KEY,
        SLACK_CHANNEL_NAME_KEY,
    ):
        await svc.delete_for_bu(slug, k)
    await db.commit()


@router.post("/slack/test", response_model=SlackTestResult)
async def test_slack_token(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Re-verify the saved bot token. Useful for catching a revoked token
    before someone wonders why notifications stopped arriving."""
    from app.services import slack as slack_svc

    slug = _require_bu(bu)
    svc = _config_svc(db)
    token = await svc.get_for_bu(slug, SLACK_BOT_TOKEN_KEY)
    if not token:
        raise HTTPException(status_code=400, detail="No Slack token configured")
    try:
        identity = await slack_svc.verify_token(token)
    except slack_svc.SlackError as e:
        return SlackTestResult(ok=False, detail=f"Slack error: {e.code}")
    except httpx.RequestError as e:
        return SlackTestResult(ok=False, detail=f"Network error: {e!s}")
    return SlackTestResult(
        ok=True,
        team=identity.team,
        bot_user_id=identity.bot_user_id,
        url=identity.url,
    )


@router.get("/slack/channels", response_model=list[SlackChannelOut])
async def list_slack_channels(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List channels the bot can see. Used by the Settings UI to populate
    the channel dropdown after the operator has verified the token.

    Returns both public (`channels:read`) and private (`groups:read` +
    bot must be invited) channels in one list. The `is_private` flag on
    each row lets the UI render a lock badge so operators know which one
    they're about to pick.
    """
    from app.services import slack as slack_svc

    slug = _require_bu(bu)
    svc = _config_svc(db)
    token = await svc.get_for_bu(slug, SLACK_BOT_TOKEN_KEY)
    if not token:
        raise HTTPException(status_code=400, detail="No Slack token configured")
    try:
        channels = await slack_svc.list_channels(token)
    except slack_svc.SlackError as e:
        raise HTTPException(
            status_code=400,
            detail=(
                "missing_scope: bot needs channels:read (public) and/or groups:read (private)"
                if e.code == "missing_scope"
                else f"Slack error: {e.code}"
            ),
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Slack unreachable: {e!s}")
    return [
        SlackChannelOut(id=c.id, name=c.name, is_private=c.is_private)
        for c in channels
    ]
