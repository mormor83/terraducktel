"""Thin wrapper around the Telegram Bot API.

Used by the integrations router (verify token, resolve chat, send a test
message) and by the notification hooks (post run / drift events). The bot
token + chat id are persisted per-BU in the encrypted `config` table; this
module only talks HTTP, it never reads / decrypts on its own.

All public functions are async and either return a structured dataclass or
raise TelegramError on a hard failure. Network errors bubble up as
httpx.RequestError so the caller can decide whether to log + drop
(best-effort notification) or surface as 5xx (verify endpoint).

Messages go out with parse_mode=HTML. Telegram's MarkdownV2 demands a
backslash before any of `_*[]()~`>#+-=|{}.!` *anywhere* in the text,
including inside literal content — and branch names, leaf paths and plan
excerpts are full of `-`, `.` and `_`. HTML needs only &, < and > escaped
and covers every construct the Slack Block Kit messages use.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_BASE = "https://api.telegram.org"
_TIMEOUT = 10.0

# `sendMessage` hard-caps `text` at 4096 characters. We cut well below the
# cap so the truncation marker and any closing tags always fit.
MAX_MESSAGE_CHARS = 4096
_TRUNCATE_AT = 3900
_TRUNCATION_MARKER = "\n…(truncated)"

# Numeric (negative for groups / supergroups, positive for a private chat)
# or a public @username. Telegram usernames are 5-32 chars, letters, digits
# and underscores, starting with a letter.
CHAT_ID_RE = re.compile(r"^(-?\d+|@[A-Za-z][A-Za-z0-9_]{4,31})$")

# Only the tags we actually emit. A malformed tag we never generate is not
# worth guarding against here.
_TAG_RE = re.compile(r"<(/?)(b|i|code|pre|a)(?:\s[^>]*)?>")


class TelegramError(Exception):
    """Telegram answered ok=false.

    `code` is Telegram's numeric `error_code`: 401 revoked / malformed token,
    400 bad request (chat not found, unparsable entities, bad button URL),
    403 bot blocked or kicked or not a channel admin, 429 rate limited.
    """

    def __init__(self, code: int, description: str = ""):
        super().__init__(description or f"telegram error {code}")
        self.code = code
        self.description = description


@dataclass(frozen=True)
class TelegramIdentity:
    """Result of `getMe` — confirms the token works and names the bot. Cached
    on the integration so Settings can show "@tdt_bot" without re-hitting
    Telegram on every page load."""

    bot_id: int
    username: str  # without the leading @
    first_name: str


@dataclass(frozen=True)
class TelegramChat:
    id: str
    title: str  # `title` for group/supergroup/channel, `first_name` for private
    type: str  # private | group | supergroup | channel


def _esc(value) -> str:
    """Escape one interpolated value for parse_mode=HTML.

    `&` MUST be replaced first: doing `<` first would turn the `&` of the
    resulting `&lt;` into `&amp;lt;`, and Telegram would render the literal
    text "&lt;" instead of a less-than sign.
    """
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _close_open_tags(html: str) -> str:
    """Append closing tags for any element the text left open.

    Truncation can cut between `<pre>` and `</pre>`; Telegram then rejects the
    entire message with "can't parse entities" and the notification is lost.
    """
    stack: list[str] = []
    for closing, name in _TAG_RE.findall(html):
        if closing:
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
    return html + "".join(f"</{n}>" for n in reversed(stack))


def _truncate(text: str) -> str:
    """Keep `text` under Telegram's 4096-char cap, on a line boundary.

    A safety net, not a formatter: every variable-length field is already
    capped at its source (error excerpt 600 chars, drift summary 800), so in
    practice this should not fire.
    """
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    cut = text[:_TRUNCATE_AT]
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return _close_open_tags(cut + _TRUNCATION_MARKER)


async def _call(token: str, method: str, payload: dict | None = None) -> dict:
    """POST one Bot API method and return its `result`.

    Raises TelegramError when Telegram answers ok=false or with a non-JSON
    body (a proxy's HTML error page, say). httpx.RequestError propagates.
    """
    url = f"{_TELEGRAM_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(url, json=payload or {})
    try:
        data = r.json()
    except ValueError:
        raise TelegramError(
            code=getattr(r, "status_code", 0) or 0,
            description="non-JSON response from Telegram",
        )
    if not data.get("ok"):
        raise TelegramError(
            code=int(data.get("error_code") or getattr(r, "status_code", 0) or 0),
            description=str(data.get("description") or ""),
        )
    return data.get("result") or {}


async def verify_token(token: str) -> TelegramIdentity:
    """Call `getMe`. Raises TelegramError(401) on a revoked or malformed token."""
    result = await _call(token, "getMe")
    return TelegramIdentity(
        bot_id=int(result.get("id") or 0),
        username=result.get("username") or "",
        first_name=result.get("first_name") or "",
    )


async def get_chat(token: str, chat_id: str) -> TelegramChat:
    """Resolve a chat the bot can see. Raises TelegramError(400) with
    "chat not found" when the bot was never added to it.

    Note this proves the bot can SEE the chat, not that it may POST to it —
    in a channel the bot must be an administrator, and `getChat` succeeds
    either way. That is why the integration also offers a test-message
    endpoint.
    """
    result = await _call(token, "getChat", {"chat_id": chat_id})
    # Groups, supergroups and channels carry `title`; a private chat with a
    # user carries `first_name` instead.
    return TelegramChat(
        id=str(result.get("id") or chat_id),
        title=result.get("title") or result.get("first_name") or "",
        type=result.get("type") or "",
    )


async def send_message(
    token: str,
    chat_id: str,
    text: str,
    buttons: list[tuple[str, str]] | None = None,
) -> None:
    """Post an HTML message to a chat. Raises TelegramError on failure.

    `text` must already be escaped by the caller (see `_esc`) — callers build
    their own markup around escaped values, so escaping here would double it.

    `buttons` is a list of (label, url) rendered as one row of inline URL
    buttons. Telegram rejects any inline button whose URL is not https, so if
    any URL fails that test the whole keyboard is dropped rather than the send
    failing: `PUBLIC_UI_URL` defaults to `http://localhost:8000` in dev, and
    senders always repeat the link inline in the body anyway.
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": _truncate(text),
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if buttons and all(url.startswith("https://") for _, url in buttons):
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": t, "url": u} for t, u in buttons]]
        }
    await _call(token, "sendMessage", payload)
