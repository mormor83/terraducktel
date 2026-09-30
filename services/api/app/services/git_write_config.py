"""Per-BU git write access for environment promotions.

Strictly per Business Unit — every key lives under `bu.<slug>.promotion.*` and
is read with `ConfigService.get(bu_key(...))`, never `get_for_bu` (which falls
back to global keys) and never an env var. A BU that hasn't opted in cannot
push, even if its read PAT happens to have write scope: `git_write_enabled`
defaults to off, because the first promotion a BU makes should be one it
decided to be able to make.

Credential: the dedicated `promotion.git_token` when set, else the BU's own
`github.token` (its read PAT, if that one can push). The push check asks the
forge itself (`permissions.push` on the repo — GitHub's REST API, or the
Gitea/Forgejo API for anything else), so Settings shows exactly which repos a
promotion could write to.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.services.config_service import ConfigService
from app.services.git_repo import Creds

ENABLED_KEY = "promotion.git_write_enabled"
TOKEN_KEY = "promotion.git_token"
USERNAME_KEY = "promotion.git_username"
BOT_NAME_KEY = "promotion.bot_name"
BOT_EMAIL_KEY = "promotion.bot_email"
GITHUB_TOKEN_KEY = "github.token"

DEFAULT_BOT_NAME = "Terraducktel"
DEFAULT_BOT_EMAIL = "terraducktel-bot@users.noreply.terraducktel.local"


@dataclass
class WriteConfig:
    enabled: bool
    token: Optional[str]
    token_source: Optional[str]  # "dedicated" | "github" | None
    username: Optional[str]
    bot_name: str
    bot_email: str

    def creds(self) -> Creds:
        return Creds(username=self.username or "x-access-token", token=self.token)


def _svc(db: AsyncSession) -> ConfigService:
    return ConfigService(db, get_credential_encryption_key())


async def load(db: AsyncSession, bu_slug: str) -> WriteConfig:
    svc = _svc(db)
    k = lambda key: svc.bu_key(bu_slug, key)  # noqa: E731
    enabled = (await svc.get(k(ENABLED_KEY)) or "").strip().lower() in ("true", "1", "yes")
    dedicated = (await svc.get(k(TOKEN_KEY)) or "").strip()
    gh = (await svc.get(k(GITHUB_TOKEN_KEY)) or "").strip()
    token, source = (dedicated, "dedicated") if dedicated else ((gh, "github") if gh else (None, None))
    return WriteConfig(
        enabled=enabled,
        token=token,
        token_source=source,
        username=(await svc.get(k(USERNAME_KEY)) or "").strip() or None,
        bot_name=(await svc.get(k(BOT_NAME_KEY)) or "").strip() or DEFAULT_BOT_NAME,
        bot_email=(await svc.get(k(BOT_EMAIL_KEY)) or "").strip() or DEFAULT_BOT_EMAIL,
    )


async def save(
    db: AsyncSession,
    bu_slug: str,
    user_id: str,
    *,
    enabled: Optional[bool] = None,
    token: Optional[str] = None,
    clear_token: bool = False,
    username: Optional[str] = None,
    bot_name: Optional[str] = None,
    bot_email: Optional[str] = None,
) -> None:
    svc = _svc(db)
    if enabled is not None:
        await svc.set_for_bu(bu_slug, ENABLED_KEY, "true" if enabled else "false",
                             description="Allow environment promotions to push commits", updated_by=user_id)
    if clear_token:
        await svc.delete_for_bu(bu_slug, TOKEN_KEY)
    elif token:
        await svc.set_for_bu(bu_slug, TOKEN_KEY, token.strip(), is_secret=True,
                             description="Git write token for environment promotions", updated_by=user_id)
    for key, val in ((USERNAME_KEY, username), (BOT_NAME_KEY, bot_name), (BOT_EMAIL_KEY, bot_email)):
        if val is not None:
            if val.strip():
                await svc.set_for_bu(bu_slug, key, val.strip(), updated_by=user_id)
            else:
                await svc.delete_for_bu(bu_slug, key)


def mask_tail(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return "…" + token[-4:] if len(token) > 8 else "…"


def _owner_repo(repo_url: str) -> tuple[str, str, str] | None:
    """(api base, owner, repo) for an https forge URL, or None."""
    p = urllib.parse.urlparse(repo_url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if p.hostname == "github.com":
        return ("https://api.github.com", owner, repo)
    port = f":{p.port}" if p.port else ""
    return (f"{p.scheme}://{p.hostname}{port}/api/v1", owner, repo)


async def check_push(repo_url: str, cfg: WriteConfig) -> dict:
    """Ask the forge whether `cfg`'s token can push to `repo_url`. Never raises."""
    out = {"repo_url": repo_url, "ok": False, "can_push": False, "detail": None}
    if not cfg.enabled:
        out["detail"] = "Git write access is disabled for this Business Unit"
        return out
    if not cfg.token:
        out["detail"] = "No git write token (set a dedicated token, or a GitHub token for this BU)"
        return out
    target = _owner_repo(repo_url)
    if target is None:
        out["detail"] = "Only https repositories can be written to"
        return out
    base, owner, repo = target
    github = base == "https://api.github.com"
    headers = {"User-Agent": "terraducktel"}
    headers["Authorization"] = f"Bearer {cfg.token}" if github else f"token {cfg.token}"
    if github:
        headers["Accept"] = "application/vnd.github+json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/repos/{owner}/{repo}", headers=headers)
    except httpx.RequestError as e:
        out["detail"] = f"Network error: {type(e).__name__}"
        return out
    if r.status_code != 200:
        out["detail"] = f"Repository lookup returned {r.status_code}"
        return out
    perms = (r.json() or {}).get("permissions") or {}
    out["ok"] = True
    out["can_push"] = bool(perms.get("push") or perms.get("admin"))
    if not out["can_push"]:
        out["detail"] = "The token can read this repository but cannot push to it"
    return out
