"""Thin wrapper around the Slack Web API.

Used by the integrations router (verify token, list channels) and by the
notification hook (post messages on run events). The bot token + channel
id are persisted per-BU in the encrypted `config` table; this module only
talks HTTP, it never reads / decrypts on its own.

All public functions are async and return either a structured dataclass
on success or raise SlackError on a hard failure. Network errors bubble
up as httpx.RequestError so the caller can decide whether to log + drop
(best-effort notification) or surface as 5xx (verify endpoint).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_SLACK_BASE = "https://slack.com/api"
_TIMEOUT = 10.0


class SlackError(Exception):
    """Slack returned ok=false. Carries the Slack error code as `code`."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class SlackIdentity:
    """Result of `auth.test` — confirms the token works and tells us which
    workspace it's bound to. Cached on the integration so the Settings UI
    can show "connected to acme-co" without re-hitting Slack every load."""
    team: str
    team_id: str
    bot_user_id: str
    url: str


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str
    is_private: bool


async def _post(token: str, method: str, payload: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_SLACK_BASE}/{method}", headers=headers, json=payload or {}
        )
    data = r.json()
    if not data.get("ok"):
        raise SlackError(code=data.get("error", "unknown"), message=str(data))
    return data


async def _get(token: str, method: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_SLACK_BASE}/{method}", headers=headers, params=params or {}
        )
    data = r.json()
    if not data.get("ok"):
        raise SlackError(code=data.get("error", "unknown"), message=str(data))
    return data


async def verify_token(token: str) -> SlackIdentity:
    """Call `auth.test`. Raises SlackError on invalid_auth / missing_scope etc.

    The bot needs at least the `chat:write` scope to post and
    `channels:read` to list public channels — surfaced to the operator via
    the error code when listing channels fails.
    """
    data = await _post(token, "auth.test")
    return SlackIdentity(
        team=data.get("team", ""),
        team_id=data.get("team_id", ""),
        bot_user_id=data.get("bot_user_id") or data.get("user_id", ""),
        url=data.get("url", ""),
    )


async def list_channels(token: str, limit: int = 1000) -> list[SlackChannel]:
    """List channels the bot can see — both public and private.

    Private channels require the bot to be invited AND for the token to
    carry the `groups:read` scope; the call still succeeds with only
    `channels:read` but returns no private rows.

    Slack quirks to know about:
    - `conversations.list` accepts `limit` up to 1000; we request that.
    - Even with `exclude_archived=true`, pages can come back smaller than
      `limit` because archived rows count against the page before being
      filtered, so we MUST follow `next_cursor` until it's empty rather
      than stopping when a page is "short". The previous 5-page cap with
      a default `limit` ended up truncating large workspaces around 500
      channels — that's why this bumps both the per-page size and the
      cap, and keeps following cursors until Slack stops returning one.
    - `conversations.list` is tier-2 (~20 req/min). 1000 per page lets a
      ~20k-channel workspace finish inside one minute's budget.
    """
    out: list[SlackChannel] = []
    cursor: Optional[str] = None
    # Hard ceiling at 50 pages * 1000 = 50k channels. Practically nobody
    # has that many; if you do, type the channel id directly into the
    # input rather than scrolling the dropdown.
    pages = 0
    MAX_PAGES = 50
    while pages < MAX_PAGES:
        params = {
            "exclude_archived": "true",
            "limit": str(limit),
            "types": "public_channel,private_channel",
        }
        if cursor:
            params["cursor"] = cursor
        data = await _get(token, "conversations.list", params)
        for c in data.get("channels", []) or []:
            out.append(
                SlackChannel(
                    id=c.get("id", ""),
                    name=c.get("name", ""),
                    is_private=bool(c.get("is_private")),
                )
            )
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
        pages += 1
    return out


async def post_message(token: str, channel: str, text: str, blocks: list | None = None) -> None:
    """Post a message to a channel. Raises SlackError on failure.

    Callers used in notification hooks should wrap this in try/except and
    log — a transient Slack outage must not break the run loop."""
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    await _post(token, "chat.postMessage", payload)
