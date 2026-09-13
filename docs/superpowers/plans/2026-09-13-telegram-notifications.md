# Telegram Notification Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver run and drift notifications to a per-Business-Unit Telegram chat, covering the same four events the Slack integration covers.

**Architecture:** A new `telegram.py` HTTP wrapper mirrors `slack.py`; four `send_telegram_*` senders sit beside the existing `send_slack_*` senders in `notification_service.py` and are fanned out from the same dispatch points; five admin-gated endpoints in the existing integrations router store a bot token and chat id as per-BU encrypted config rows. The Slack path is not refactored — Telegram is added alongside it, and each channel's send is wrapped separately so one channel's outage cannot suppress the other.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async / httpx / pytest-asyncio (API); React + TypeScript + Tailwind (UI). Telegram Bot API over HTTPS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-13-telegram-notifications-design.md`

## Global Constraints

- **No new environment variables.** All config lives in the encrypted Postgres `config` table via `ConfigService`. Per-BU keys go through `set_for_bu` / `get_for_bu` / `delete_for_bu`, which namespace them as `bu.<slug>.<key>`.
- **No migration.** These are rows in the existing `config` table; no schema change.
- **Secrets never leave the API.** No endpoint may return `telegram.bot_token`. GET returns `configured: bool` plus a masked tail from the existing `_mask_tail()` helper.
- **The bot token is stored with `is_secret=True`.** The three cached/display keys (`telegram.chat_id`, `telegram.chat_title`, `telegram.bot_username`) are `is_secret=False`.
- **Every integration endpoint is `require_role(Role.admin)` and BU-scoped** through the existing `_require_bu(bu)` helper.
- **All notification sends are best-effort.** A `TelegramError` or `httpx.RequestError` is logged at WARNING and swallowed; a notification failure must never break a run transition or the drift loop.
- **Messages use `parse_mode: "HTML"`,** never MarkdownV2. Every interpolated value passes through `_esc()`.
- **Do not modify the existing Slack senders,** `slack.py`, or the legacy `slack.webhook_url` paths (`send_plan_approval_notification`, `send_drift_alert`). The only edits to existing notification code are additive fan-out calls.
- **Reuse, do not duplicate,** these existing helpers in `notification_service.py`: `_resolve_bu_slug_for_workspace`, `_account_badge`, `_leaf_path`, `_run_link`, `_workspace_link`, `_plan_summary_str`.
- **Conventional commits** (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`). Commit messages explain *why*.
- **No new Tailwind `sky-*` colours** in UI work; use `brand-*` / `accent-*` / existing slate-and-emerald patterns.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit
  ```
- API tests run from `services/api` with `python -m pytest`. Full suite: `make test-api` from the repo root.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `services/api/app/services/telegram.py` | **Create.** Bot API HTTP wrapper: `verify_token`, `get_chat`, `send_message`, HTML escaping, length guard, `TelegramError`. No DB access. | 1 |
| `services/api/tests/test_telegram_service.py` | **Create.** Unit coverage for the wrapper with `httpx` mocked. | 1 |
| `services/api/app/routers/integrations.py` | **Modify.** Four key constants + five endpoints + four Pydantic schemas. | 2 |
| `services/api/tests/test_telegram_integration_api.py` | **Create.** Endpoint coverage: RBAC, BU scoping, validation, secret non-disclosure. | 2 |
| `services/api/app/services/notification_service.py` | **Modify.** `send_telegram_bot_notification` + four senders + `_tg_fields` + `_telegram_bot_creds`. | 3 |
| `services/api/tests/test_notification_service.py` | **Modify.** Extend with Telegram sender coverage. | 3 |
| `services/api/app/routers/runs.py` | **Modify.** Rename `slack_bot_events` → `bot_events`; dispatch to both channels. | 4 |
| `services/api/app/routers/internal.py` | **Modify.** Add the Telegram drift call beside the Slack one. | 4 |
| `services/api/tests/test_run_notification_fanout.py` | **Create.** Both channels fire; one channel's failure does not suppress the other. | 4 |
| `services/ui/src/pages/Settings.tsx` | **Modify.** `TelegramSection` component, `ICON.telegram`, new tab entry. | 5 |
| `docs/API.md`, `docs/ARCHITECTURE.md`, `README.md` | **Modify.** Document the endpoints and the third notification channel. | 6 |

---

### Task 1: Telegram Bot API wrapper

**Files:**
- Create: `services/api/app/services/telegram.py`
- Test: `services/api/tests/test_telegram_service.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Pattern reference only: `services/api/app/services/slack.py`.
- Produces, for Tasks 2 and 3:
  - `TelegramError(code: int, description: str)` — exception, attributes `.code` (int) and `.description` (str)
  - `TelegramIdentity(bot_id: int, username: str, first_name: str)` — frozen dataclass
  - `TelegramChat(id: str, title: str, type: str)` — frozen dataclass
  - `CHAT_ID_RE` — compiled `re.Pattern`, use `CHAT_ID_RE.match(value)` to validate a chat id
  - `async verify_token(token: str) -> TelegramIdentity`
  - `async get_chat(token: str, chat_id: str) -> TelegramChat`
  - `async send_message(token: str, chat_id: str, text: str, buttons: list[tuple[str, str]] | None = None) -> None`
  - `_esc(s: str) -> str` — HTML-escape one interpolated value (module-private, but Task 3 imports it)

- [ ] **Step 1: Write the failing tests**

Create `services/api/tests/test_telegram_service.py`:

```python
"""Unit coverage for services/telegram.py — the Telegram Bot API wrapper.

httpx.AsyncClient is monkeypatched; no test here touches the network.
"""
import httpx
import pytest

from app.services import telegram as tg

# No pytestmark: pyproject sets asyncio_mode = "auto", so async tests need no
# mark, and a blanket asyncio mark would wrongly decorate this module's sync
# tests.


class _FakeResponse:
    def __init__(self, payload, status_code=200, text_body=None):
        self._payload = payload
        self.status_code = status_code
        self._text_body = text_body

    def json(self):
        if self._text_body is not None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Captures the last (url, json) posted and replays a queued response."""

    next_response = None
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kw):
        _FakeClient.calls.append((url, json))
        resp = _FakeClient.next_response
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.next_response = _FakeResponse({"ok": True, "result": {}})
    monkeypatch.setattr(tg.httpx, "AsyncClient", _FakeClient)
    return _FakeClient


# ─── escaping ────────────────────────────────────────────────────────────────


def test_esc_escapes_ampersand_first():
    # If `<` were replaced before `&`, the `&` of `&lt;` would be re-escaped
    # into `&amp;lt;` and Telegram would render the literal text "&lt;".
    assert tg._esc("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_esc_handles_none_and_numbers():
    assert tg._esc(None) == ""
    assert tg._esc(42) == "42"


# ─── getMe / getChat ─────────────────────────────────────────────────────────


async def test_verify_token_parses_get_me(fake_http):
    fake_http.next_response = _FakeResponse(
        {"ok": True, "result": {"id": 777, "is_bot": True,
                                "first_name": "TDT", "username": "tdt_bot"}}
    )
    ident = await tg.verify_token("123:secret")
    assert ident.bot_id == 777
    assert ident.username == "tdt_bot"
    assert ident.first_name == "TDT"
    url, _ = fake_http.calls[-1]
    assert url.endswith("/bot123:secret/getMe")


async def test_not_ok_raises_with_numeric_code(fake_http):
    fake_http.next_response = _FakeResponse(
        {"ok": False, "error_code": 401, "description": "Unauthorized"}
    )
    with pytest.raises(tg.TelegramError) as ei:
        await tg.verify_token("bad")
    assert ei.value.code == 401
    assert "Unauthorized" in ei.value.description


async def test_rate_limit_surfaces_as_429(fake_http):
    fake_http.next_response = _FakeResponse(
        {"ok": False, "error_code": 429,
         "description": "Too Many Requests: retry after 30"}
    )
    with pytest.raises(tg.TelegramError) as ei:
        await tg.send_message("t", "-100", "hi")
    assert ei.value.code == 429


async def test_non_json_response_raises_with_http_status(fake_http):
    fake_http.next_response = _FakeResponse(None, status_code=502, text_body="<html>bad gateway")
    with pytest.raises(tg.TelegramError) as ei:
        await tg.verify_token("t")
    assert ei.value.code == 502


async def test_get_chat_uses_title_for_supergroup(fake_http):
    fake_http.next_response = _FakeResponse(
        {"ok": True, "result": {"id": -1001234567890, "title": "Platform Ops",
                                "type": "supergroup"}}
    )
    chat = await tg.get_chat("t", "-1001234567890")
    assert chat.title == "Platform Ops"
    assert chat.type == "supergroup"
    assert chat.id == "-1001234567890"


async def test_get_chat_falls_back_to_first_name_for_private(fake_http):
    # A private chat has no `title`; without the fallback the Settings UI
    # would show an empty chat name after a successful verify.
    fake_http.next_response = _FakeResponse(
        {"ok": True, "result": {"id": 555, "first_name": "Pavel", "type": "private"}}
    )
    chat = await tg.get_chat("t", "555")
    assert chat.title == "Pavel"
    assert chat.type == "private"


# ─── sendMessage ─────────────────────────────────────────────────────────────


async def test_send_message_sets_html_mode_and_disables_previews(fake_http):
    await tg.send_message("t", "-100", "<b>hi</b>")
    _, payload = fake_http.calls[-1]
    assert payload["parse_mode"] == "HTML"
    assert payload["chat_id"] == "-100"
    assert payload["text"] == "<b>hi</b>"
    assert payload["link_preview_options"] == {"is_disabled": True}


async def test_https_buttons_render_as_inline_keyboard(fake_http):
    await tg.send_message("t", "-100", "hi",
                          buttons=[("View run", "https://tdt.example.com/runs/1")])
    _, payload = fake_http.calls[-1]
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "View run", "url": "https://tdt.example.com/runs/1"}]]
    }


async def test_non_https_button_is_dropped_but_message_still_sends(fake_http):
    # Telegram rejects a non-https inline button URL outright. PUBLIC_UI_URL
    # defaults to http://localhost:8000 in dev, so failing the send here would
    # break every notification on a developer machine.
    await tg.send_message("t", "-100", "hi",
                          buttons=[("View run", "http://localhost:8000/runs/1")])
    _, payload = fake_http.calls[-1]
    assert "reply_markup" not in payload
    assert payload["text"] == "hi"


# ─── length guard ────────────────────────────────────────────────────────────


async def test_long_message_is_truncated_below_the_cap(fake_http):
    text = "\n".join(f"line {i}" for i in range(2000))
    assert len(text) > tg.MAX_MESSAGE_CHARS
    await tg.send_message("t", "-100", text)
    _, payload = fake_http.calls[-1]
    assert len(payload["text"]) <= tg.MAX_MESSAGE_CHARS
    assert payload["text"].endswith("…(truncated)")


async def test_truncation_closes_tags_left_open():
    # Cutting between <pre> and </pre> makes Telegram reject the whole
    # message with "can't parse entities".
    # The excerpt must contain newlines INSIDE the <pre>: _truncate cuts back
    # to the last newline, so a single pre-block with no internal newlines
    # would rewind past the opening tag and never exercise the closer.
    body = "\n".join("x" * 40 for _ in range(300))
    text = "<b>head</b>\n<pre>" + body + "</pre>"
    out = tg._truncate(text)
    assert len(out) <= tg.MAX_MESSAGE_CHARS
    assert out.endswith("</pre>")
    assert out.count("<pre>") == out.count("</pre>")


def test_close_open_tags_handles_nesting():
    assert tg._close_open_tags("<b>a<i>b") == "<b>a<i>b</i></b>"
    assert tg._close_open_tags("<b>a</b>") == "<b>a</b>"
    assert tg._close_open_tags('<a href="https://x">link') == '<a href="https://x">link</a>'


# ─── chat id validation ──────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["-1001234567890", "12345678", "-987654", "@my_channel"])
def test_chat_id_accepts_valid(value):
    assert tg.CHAT_ID_RE.match(value)


@pytest.mark.parametrize("value", ["", "abc", "@no", "12 34", "https://t.me/x", "@bad-dash"])
def test_chat_id_rejects_invalid(value):
    assert not tg.CHAT_ID_RE.match(value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_telegram_service.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'app.services.telegram'`.

- [ ] **Step 3: Write the implementation**

Create `services/api/app/services/telegram.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_telegram_service.py -q`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add services/api/app/services/telegram.py services/api/tests/test_telegram_service.py
git commit -m "feat(api): Telegram Bot API wrapper

HTML parse mode rather than MarkdownV2: MarkdownV2 needs 18 characters
escaped anywhere in the text, and branch names, leaf paths and plan
excerpts are full of -, . and _. Inline buttons are dropped when the URL
is not https, because Telegram rejects such buttons outright and
PUBLIC_UI_URL is http://localhost:8000 in dev. Truncation closes any tag
it cuts open, or Telegram rejects the whole message.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 2: Integration endpoints

**Files:**
- Modify: `services/api/app/routers/integrations.py` (add key constants near line 54, after `SLACK_TEAM_NAME_KEY`; add schemas + routes at the end of the file, after `list_slack_channels`)
- Test: `services/api/tests/test_telegram_integration_api.py`

**Interfaces:**
- Consumes, from Task 1: `verify_token`, `get_chat`, `send_message`, `TelegramError` (`.code`, `.description`), `CHAT_ID_RE`.
- Produces, for Task 3:
  - `TELEGRAM_BOT_TOKEN_KEY = "telegram.bot_token"`
  - `TELEGRAM_CHAT_ID_KEY = "telegram.chat_id"`
  - `TELEGRAM_CHAT_TITLE_KEY = "telegram.chat_title"`
  - `TELEGRAM_BOT_USERNAME_KEY = "telegram.bot_username"`
- Produces, for Task 5 (UI response shapes):
  - `GET  /api/v1/integrations/telegram` → `{configured, token_tail, bot_username, chat_id, chat_title}`
  - `PUT  /api/v1/integrations/telegram` ← `{token?, chat_id?}` → same shape as GET
  - `DELETE /api/v1/integrations/telegram` → 204
  - `POST /api/v1/integrations/telegram/test` → `{ok, detail?, bot_username?, chat_title?}`
  - `POST /api/v1/integrations/telegram/test-message` → `{ok, detail?, bot_username?, chat_title?}`

Existing helpers in this file to reuse, not reimplement: `_config_svc(db)`, `_mask_tail(token)`, `_require_bu(bu)`.

- [ ] **Step 1: Write the failing tests**

Create `services/api/tests/test_telegram_integration_api.py`:

```python
"""Endpoint coverage for the per-BU Telegram integration.

app.services.telegram is monkeypatched at the router's import site so no
test here reaches api.telegram.org.
"""
import httpx
import pytest

from app.services import telegram as tg

# asyncio_mode = "auto" in pyproject.toml, so no asyncio mark is needed.
pytestmark = pytest.mark.usefixtures("default_bu")

BASE = "/api/v1/integrations/telegram"


def _h(token: str, bu: str = "default") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


@pytest.fixture
def good_telegram(monkeypatch):
    """getMe and getChat both succeed; sendMessage records its calls."""
    sent: list = []

    async def _verify(token):
        return tg.TelegramIdentity(bot_id=777, username="tdt_bot", first_name="TDT")

    async def _get_chat(token, chat_id):
        return tg.TelegramChat(id=str(chat_id), title="Platform Ops", type="supergroup")

    async def _send(token, chat_id, text, buttons=None):
        sent.append((chat_id, text))

    monkeypatch.setattr(tg, "verify_token", _verify)
    monkeypatch.setattr(tg, "get_chat", _get_chat)
    monkeypatch.setattr(tg, "send_message", _send)
    return sent


# ─── RBAC ────────────────────────────────────────────────────────────────────


async def test_viewer_and_operator_are_forbidden(
    auth_client, viewer_token, operator_token, good_telegram
):
    for tok in (viewer_token, operator_token):
        r = await auth_client.get(BASE, headers=_h(tok))
        assert r.status_code == 403


async def test_unconfigured_reports_not_configured(auth_client, admin_token):
    r = await auth_client.get(BASE, headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["configured"] is False


# ─── PUT ─────────────────────────────────────────────────────────────────────


async def test_put_requires_a_token_on_first_save(auth_client, admin_token, good_telegram):
    r = await auth_client.put(BASE, json={"chat_id": "-1001"}, headers=_h(admin_token))
    assert r.status_code == 422


async def test_put_saves_token_and_chat_and_never_returns_the_token(
    auth_client, admin_token, good_telegram
):
    secret = "777:AAHsecrettokenvalue"
    r = await auth_client.put(
        BASE, json={"token": secret, "chat_id": "-1001234567890"},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["bot_username"] == "tdt_bot"
    assert body["chat_id"] == "-1001234567890"
    assert body["chat_title"] == "Platform Ops"
    assert secret not in r.text
    assert body["token_tail"] == "…alue"

    g = await auth_client.get(BASE, headers=_h(admin_token))
    assert secret not in g.text
    assert g.json()["chat_title"] == "Platform Ops"


async def test_put_rejects_a_malformed_chat_id_before_calling_telegram(
    auth_client, admin_token, monkeypatch
):
    async def _boom(*a, **k):
        raise AssertionError("must not reach Telegram for a malformed chat id")

    monkeypatch.setattr(tg, "get_chat", _boom)

    async def _verify(token):
        return tg.TelegramIdentity(bot_id=1, username="b", first_name="B")

    monkeypatch.setattr(tg, "verify_token", _verify)

    r = await auth_client.put(
        BASE, json={"token": "1:abcdefgh", "chat_id": "https://t.me/nope"},
        headers=_h(admin_token),
    )
    assert r.status_code == 422


async def test_put_rejects_a_token_telegram_refuses(auth_client, admin_token, monkeypatch):
    async def _verify(token):
        raise tg.TelegramError(code=401, description="Unauthorized")

    monkeypatch.setattr(tg, "verify_token", _verify)
    r = await auth_client.put(BASE, json={"token": "1:badtoken"}, headers=_h(admin_token))
    assert r.status_code == 400
    assert "401" in r.text or "Unauthorized" in r.text


async def test_put_returns_502_when_telegram_is_unreachable(
    auth_client, admin_token, monkeypatch
):
    async def _verify(token):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(tg, "verify_token", _verify)
    r = await auth_client.put(BASE, json={"token": "1:abcdefgh"}, headers=_h(admin_token))
    assert r.status_code == 502


async def test_put_rejects_a_chat_the_bot_cannot_see(auth_client, admin_token, monkeypatch):
    async def _verify(token):
        return tg.TelegramIdentity(bot_id=1, username="b", first_name="B")

    async def _get_chat(token, chat_id):
        raise tg.TelegramError(code=400, description="Bad Request: chat not found")

    monkeypatch.setattr(tg, "verify_token", _verify)
    monkeypatch.setattr(tg, "get_chat", _get_chat)
    r = await auth_client.put(
        BASE, json={"token": "1:abcdefgh", "chat_id": "-1009"}, headers=_h(admin_token)
    )
    assert r.status_code == 400
    assert "chat not found" in r.text


async def test_put_without_a_token_reuses_the_stored_one(
    auth_client, admin_token, good_telegram
):
    await auth_client.put(
        BASE, json={"token": "777:firstsecret"}, headers=_h(admin_token)
    )
    r = await auth_client.put(BASE, json={"chat_id": "-1002"}, headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["chat_id"] == "-1002"
    assert r.json()["token_tail"] == "…cret"


# ─── BU scoping ──────────────────────────────────────────────────────────────


async def test_config_is_scoped_to_the_business_unit(
    auth_client, admin_token, good_telegram, _setup_db
):
    # NOTE: UserBusinessUnit lives in app.models.business_unit, not in a
    # module of its own.
    from app.models.business_unit import BusinessUnit, UserBusinessUnit
    from app.models.user import User
    from sqlalchemy import select
    import uuid

    async with _setup_db() as s:
        bu = BusinessUnit(id=str(uuid.uuid4()), slug="other", name="Other")
        s.add(bu)
        admin = (
            await s.execute(select(User).where(User.email == "admin@test.com"))
        ).scalars().first()
        s.add(UserBusinessUnit(
            id=str(uuid.uuid4()), user_id=admin.id,
            business_unit_id=bu.id, role="operator",
        ))
        await s.commit()

    await auth_client.put(
        BASE, json={"token": "777:defaultsecret", "chat_id": "-1001"},
        headers=_h(admin_token, "default"),
    )
    r = await auth_client.get(BASE, headers=_h(admin_token, "other"))
    assert r.json()["configured"] is False


# ─── test / test-message / delete ────────────────────────────────────────────


async def test_test_endpoints_400_when_nothing_is_configured(auth_client, admin_token):
    for path in (f"{BASE}/test", f"{BASE}/test-message"):
        r = await auth_client.post(path, headers=_h(admin_token))
        assert r.status_code == 400


async def test_test_reverifies_the_saved_token(auth_client, admin_token, good_telegram):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )
    r = await auth_client.post(f"{BASE}/test", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["bot_username"] == "tdt_bot"


async def test_test_reports_a_revoked_token_without_raising(
    auth_client, admin_token, good_telegram, monkeypatch
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )

    async def _verify(token):
        raise tg.TelegramError(code=401, description="Unauthorized")

    monkeypatch.setattr(tg, "verify_token", _verify)
    r = await auth_client.post(f"{BASE}/test", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "401" in r.json()["detail"]


async def test_test_message_posts_to_the_configured_chat(
    auth_client, admin_token, good_telegram
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )
    r = await auth_client.post(f"{BASE}/test-message", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert good_telegram, "expected sendMessage to be called"
    chat_id, text = good_telegram[-1]
    assert chat_id == "-1001"
    assert "Terraducktel" in text


async def test_test_message_reports_a_send_failure(
    auth_client, admin_token, good_telegram, monkeypatch
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )

    async def _send(token, chat_id, text, buttons=None):
        raise tg.TelegramError(code=403, description="Forbidden: bot is not a member")

    monkeypatch.setattr(tg, "send_message", _send)
    r = await auth_client.post(f"{BASE}/test-message", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "403" in r.json()["detail"]


async def test_delete_removes_every_key(auth_client, admin_token, good_telegram):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )
    d = await auth_client.delete(BASE, headers=_h(admin_token))
    assert d.status_code == 204
    g = await auth_client.get(BASE, headers=_h(admin_token))
    assert g.json()["configured"] is False
    assert g.json()["chat_id"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_telegram_integration_api.py -q`
Expected: FAIL — every request 404s because the routes do not exist.

- [ ] **Step 3: Add the key constants**

In `services/api/app/routers/integrations.py`, directly after the `SLACK_TEAM_NAME_KEY` line (~line 54):

```python
TELEGRAM_BOT_TOKEN_KEY = "telegram.bot_token"
TELEGRAM_CHAT_ID_KEY = "telegram.chat_id"
TELEGRAM_CHAT_TITLE_KEY = "telegram.chat_title"
TELEGRAM_BOT_USERNAME_KEY = "telegram.bot_username"
```

- [ ] **Step 4: Add schemas and routes**

Append to the end of `services/api/app/routers/integrations.py`, after `list_slack_channels`:

```python
# ─── Telegram (per-BU bot token + chat) ────────────────────────────────────
#
# Structurally parallel to the Slack block above, with one difference forced
# by the Bot API: a bot cannot enumerate the chats it belongs to, so there is
# no `/channels` equivalent. The operator types the chat id and we verify it
# with `getChat` at save time.


class TelegramStatus(BaseModel):
    """GET response: never returns the bot token. The UI sees a masked tail
    plus the cached bot username and chat title so an admin can confirm
    "this is the right bot, in the right chat"."""

    configured: bool
    token_tail: Optional[str] = None
    bot_username: Optional[str] = None
    chat_id: Optional[str] = None
    chat_title: Optional[str] = None


class TelegramUpdate(BaseModel):
    """PUT payload. `token` is required on first save; later saves may omit it
    to keep the stored one and just change the chat."""

    token: Optional[str] = Field(default=None, min_length=8)
    chat_id: Optional[str] = Field(default=None, max_length=64)


class TelegramTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    bot_username: Optional[str] = None
    chat_title: Optional[str] = None


async def _telegram_status(svc: ConfigService, slug: str) -> TelegramStatus:
    token = await svc.get_for_bu(slug, TELEGRAM_BOT_TOKEN_KEY)
    if not token:
        return TelegramStatus(configured=False)
    return TelegramStatus(
        configured=True,
        token_tail=_mask_tail(token),
        bot_username=await svc.get_for_bu(slug, TELEGRAM_BOT_USERNAME_KEY),
        chat_id=await svc.get_for_bu(slug, TELEGRAM_CHAT_ID_KEY),
        chat_title=await svc.get_for_bu(slug, TELEGRAM_CHAT_TITLE_KEY),
    )


@router.get("/telegram", response_model=TelegramStatus)
async def get_telegram_status(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await _telegram_status(_config_svc(db), _require_bu(bu))


@router.put("/telegram", response_model=TelegramStatus)
async def set_telegram_config(
    body: TelegramUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Save Telegram config, verifying against the Bot API before persisting.

    `getMe` proves the token works; `getChat` proves the bot can see the chat
    (though not that it may post there — see the test-message endpoint).
    Saving an unverified token would mean every later notification fails
    silently inside the worker.
    """
    from app.services import telegram as tg_svc

    slug = _require_bu(bu)
    svc = _config_svc(db)

    existing_token = await svc.get_for_bu(slug, TELEGRAM_BOT_TOKEN_KEY)
    token = (body.token or "").strip() or existing_token
    if not token:
        raise HTTPException(
            status_code=422, detail="Bot token is required on first save"
        )

    chat_id = (body.chat_id or "").strip() or None
    if chat_id is not None and not tg_svc.CHAT_ID_RE.match(chat_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "chat_id must be numeric (e.g. -1001234567890) or a public "
                "@username"
            ),
        )

    try:
        identity = await tg_svc.verify_token(token)
    except tg_svc.TelegramError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram rejected the token ({e.code}): {e.description}",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Telegram unreachable: {e!s}")

    chat_title: Optional[str] = None
    if chat_id is not None:
        try:
            chat = await tg_svc.get_chat(token, chat_id)
        except tg_svc.TelegramError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Telegram rejected the chat ({e.code}): {e.description}",
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Telegram unreachable: {e!s}")
        chat_title = chat.title

    await svc.set_for_bu(
        slug, TELEGRAM_BOT_TOKEN_KEY, token,
        is_secret=True,
        description=f"Telegram bot token for BU '{slug}'.",
        updated_by=current_user.id,
    )
    await svc.set_for_bu(
        slug, TELEGRAM_BOT_USERNAME_KEY, identity.username,
        is_secret=False,
        description=f"Telegram bot username (cached) for BU '{slug}'.",
        updated_by=current_user.id,
    )
    if chat_id is not None:
        await svc.set_for_bu(
            slug, TELEGRAM_CHAT_ID_KEY, chat_id,
            is_secret=False,
            description=f"Telegram chat id for BU '{slug}'.",
            updated_by=current_user.id,
        )
        await svc.set_for_bu(
            slug, TELEGRAM_CHAT_TITLE_KEY, chat_title or "",
            is_secret=False,
            description=f"Telegram chat title (cached) for BU '{slug}'.",
            updated_by=current_user.id,
        )
    await db.commit()

    return await _telegram_status(svc, slug)


@router.delete("/telegram", status_code=204)
async def delete_telegram_config(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _require_bu(bu)
    svc = _config_svc(db)
    for k in (
        TELEGRAM_BOT_TOKEN_KEY,
        TELEGRAM_BOT_USERNAME_KEY,
        TELEGRAM_CHAT_ID_KEY,
        TELEGRAM_CHAT_TITLE_KEY,
    ):
        await svc.delete_for_bu(slug, k)
    await db.commit()


@router.post("/telegram/test", response_model=TelegramTestResult)
async def test_telegram_token(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Re-verify the saved token and re-read the chat. Catches a revoked token
    or a bot removed from the group before someone wonders why notifications
    stopped arriving."""
    from app.services import telegram as tg_svc

    slug = _require_bu(bu)
    svc = _config_svc(db)
    token = await svc.get_for_bu(slug, TELEGRAM_BOT_TOKEN_KEY)
    if not token:
        raise HTTPException(status_code=400, detail="No Telegram token configured")
    try:
        identity = await tg_svc.verify_token(token)
    except tg_svc.TelegramError as e:
        return TelegramTestResult(
            ok=False, detail=f"Telegram error {e.code}: {e.description}"
        )
    except httpx.RequestError as e:
        return TelegramTestResult(ok=False, detail=f"Network error: {e!s}")

    chat_title = None
    chat_id = await svc.get_for_bu(slug, TELEGRAM_CHAT_ID_KEY)
    if chat_id:
        try:
            chat_title = (await tg_svc.get_chat(token, chat_id)).title
        except (tg_svc.TelegramError, httpx.RequestError) as e:
            return TelegramTestResult(
                ok=False,
                bot_username=identity.username,
                detail=f"Token is valid but the chat is unreachable: {e!s}",
            )
    return TelegramTestResult(
        ok=True, bot_username=identity.username, chat_title=chat_title
    )


@router.post("/telegram/test-message", response_model=TelegramTestResult)
async def send_telegram_test_message(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Actually post to the configured chat.

    `getChat` succeeding does not prove the bot may POST: in a channel the bot
    must be an administrator, and `getChat` succeeds either way. Since there is
    no channel picker to confirm the wiring, a real message is the only honest
    confirmation an operator can get.
    """
    from app.services import telegram as tg_svc

    slug = _require_bu(bu)
    svc = _config_svc(db)
    token = await svc.get_for_bu(slug, TELEGRAM_BOT_TOKEN_KEY)
    chat_id = await svc.get_for_bu(slug, TELEGRAM_CHAT_ID_KEY)
    if not token or not chat_id:
        raise HTTPException(
            status_code=400, detail="Telegram token and chat id must both be set"
        )
    try:
        await tg_svc.send_message(
            token,
            chat_id,
            "✅ <b>Terraducktel</b> is connected to this chat.",
        )
    except tg_svc.TelegramError as e:
        return TelegramTestResult(
            ok=False, detail=f"Telegram error {e.code}: {e.description}"
        )
    except httpx.RequestError as e:
        return TelegramTestResult(ok=False, detail=f"Network error: {e!s}")
    return TelegramTestResult(
        ok=True,
        chat_title=await svc.get_for_bu(slug, TELEGRAM_CHAT_TITLE_KEY),
        detail="Message sent",
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_telegram_integration_api.py -q`
Expected: PASS.

- [ ] **Step 6: Run the neighbouring suites for regressions**

Run: `cd services/api && python -m pytest tests/test_config.py tests/test_integration_e2e.py -q`
Expected: PASS — no change to existing behaviour.

- [ ] **Step 7: Commit**

```bash
git add services/api/app/routers/integrations.py services/api/tests/test_telegram_integration_api.py
git commit -m "feat(api): per-BU Telegram integration endpoints

Mirrors the Slack block: admin-gated, BU-scoped, token encrypted at rest
and never returned. No /channels equivalent exists because the Bot API
cannot enumerate a bot's chats, so the chat id is typed and verified with
getChat. test-message exists because getChat proves the bot can SEE a chat,
not that it may post there — in a channel it must be an admin.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 3: Notification senders

**Files:**
- Modify: `services/api/app/services/notification_service.py` (append after `send_slack_drift_detected`, before `send_email_notification`)
- Test: `services/api/tests/test_notification_service.py` (extend)

**Interfaces:**
- Consumes, from Task 1: `send_message`, `TelegramError`, `_esc`.
- Consumes, from Task 2: `TELEGRAM_BOT_TOKEN_KEY`, `TELEGRAM_CHAT_ID_KEY`.
- Consumes, existing in this file (do not modify): `_config_svc(session)`, `_resolve_bu_slug_for_workspace(session, workspace_id) -> str | None`, `_account_badge(session, workspace_id) -> _AccountBadge` (fields `.label`, `.hex`, `.emoji`), `_leaf_path(working_dir, region) -> str`, `_run_link(run_id) -> str`, `_workspace_link(workspace_id) -> str`, `_plan_summary_str(add, change, destroy) -> str`.
- Produces, for Task 4 — each takes exactly the same keyword arguments as its Slack twin:
  - `async send_telegram_run_auto_approved(session, *, workspace_id, workspace_name, run_id, skip_apply, environment=None, region=None, working_dir=None, branch=None, command=None, triggered_by_email=None)`
  - `async send_telegram_run_awaiting_approval(session, *, workspace_id, workspace_name, run_id, environment=None, region=None, working_dir=None, branch=None, command=None, triggered_by_email=None, add=None, change=None, destroy=None)`
  - `async send_telegram_run_failed(session, *, workspace_id, workspace_name, run_id, command, environment=None, region=None, working_dir=None, branch=None, triggered_by_email=None, failed_stage=None, error_excerpt=None)`
  - `async send_telegram_drift_detected(session, *, workspace_id, workspace_name, summary, environment=None, region=None, working_dir=None)`

Note: `_account_badge().label` already contains Slack backtick markup (`` Name (`123`) ``). The Telegram renderer strips backticks with `.replace("`", "")` before escaping — the same treatment `send_email_notification`'s callers already apply.

- [ ] **Step 1: Write the failing tests**

Append to `services/api/tests/test_notification_service.py`:

```python
# ─── Telegram bot notifications ──────────────────────────────────────────────


class _FakeTelegram:
    """Records sends; raise_with makes the next send fail."""

    sent: list = []
    raise_with = None

    @classmethod
    def reset(cls):
        cls.sent = []
        cls.raise_with = None

    @classmethod
    async def send_message(cls, token, chat_id, text, buttons=None):
        if cls.raise_with is not None:
            raise cls.raise_with
        cls.sent.append({"token": token, "chat_id": chat_id,
                         "text": text, "buttons": buttons})


@pytest.fixture
def fake_telegram(monkeypatch):
    from app.services import telegram as tg

    _FakeTelegram.reset()
    monkeypatch.setattr(tg, "send_message", _FakeTelegram.send_message)
    return _FakeTelegram


async def _configure_telegram(session, bu="default", chat_id="-1001234567890"):
    await _set(session, "telegram.bot_token", "777:secret", bu=bu)
    await _set(session, "telegram.chat_id", chat_id, bu=bu)


@pytest.mark.asyncio
async def test_telegram_noop_when_unconfigured(db_session, fake_telegram):
    await ns.send_telegram_bot_notification(
        db_session, bu_slug="default", text="hello"
    )
    assert fake_telegram.sent == []


@pytest.mark.asyncio
async def test_telegram_noop_when_chat_id_missing(db_session, fake_telegram):
    await _set(db_session, "telegram.bot_token", "777:secret", bu="default")
    await ns.send_telegram_bot_notification(
        db_session, bu_slug="default", text="hello"
    )
    assert fake_telegram.sent == []


@pytest.mark.asyncio
async def test_telegram_posts_to_configured_chat(db_session, fake_telegram):
    await _configure_telegram(db_session)
    await ns.send_telegram_bot_notification(
        db_session, bu_slug="default", text="hello",
        buttons=[("View run", "https://x.example.com/runs/1")],
    )
    assert len(fake_telegram.sent) == 1
    assert fake_telegram.sent[0]["chat_id"] == "-1001234567890"
    assert fake_telegram.sent[0]["text"] == "hello"
    assert fake_telegram.sent[0]["buttons"] == [
        ("View run", "https://x.example.com/runs/1")
    ]


@pytest.mark.asyncio
async def test_telegram_swallows_telegram_error(db_session, fake_telegram):
    from app.services import telegram as tg

    await _configure_telegram(db_session)
    fake_telegram.raise_with = tg.TelegramError(code=403, description="kicked")
    # Must not raise — a notification failure may never break the caller.
    await ns.send_telegram_bot_notification(
        db_session, bu_slug="default", text="hello"
    )


@pytest.mark.asyncio
async def test_telegram_swallows_network_error(db_session, fake_telegram):
    await _configure_telegram(db_session)
    fake_telegram.raise_with = httpx.ConnectError("down")
    await ns.send_telegram_bot_notification(
        db_session, bu_slug="default", text="hello"
    )


def test_tg_fields_skips_empty_values():
    out = ns._tg_fields([("Account", "Prod"), ("Branch", ""), ("Region", "us-east-1")])
    assert "<b>Account</b> Prod" in out
    assert "Branch" not in out
    assert "<b>Region</b> us-east-1" in out


def test_tg_fields_escapes_values():
    out = ns._tg_fields([("Workspace", "a<b>&c")])
    assert "a&lt;b&gt;&amp;c" in out
    assert "<b>Workspace</b>" in out


@pytest.mark.asyncio
async def test_telegram_awaiting_approval_renders_plan_and_link(
    db_session, fake_telegram, monkeypatch
):
    monkeypatch.setenv("PUBLIC_UI_URL", "https://tdt.example.com")
    await _configure_telegram(db_session)
    ws = Workspace(
        business_unit_id=DEFAULT_BU_ID,
        id="ws-tg-1",
        name="prod-vpc",
        repo_url="https://example.com/r.git",
        tf_working_dir="account-222222222222/us-east-1/relprod/ai-cog",
        aws_account_id="222222222222",
        environment="prod",
        region="us-east-1",
    )
    db_session.add(ws)
    await db_session.commit()

    await ns.send_telegram_run_awaiting_approval(
        db_session,
        workspace_id="ws-tg-1",
        workspace_name="prod-vpc",
        run_id="run-1",
        region="us-east-1",
        working_dir="account-222222222222/us-east-1/relprod/ai-cog",
        branch="main",
        command="plan",
        triggered_by_email="operator@test.com",
        add=3, change=1, destroy=0,
    )
    assert len(fake_telegram.sent) == 1
    text = fake_telegram.sent[0]["text"]
    assert "Awaiting approval" in text
    assert "prod-vpc" in text
    assert "+3 ~1 −0" in text
    assert "relprod/ai-cog" in text
    assert "https://tdt.example.com/runs/run-1" in text
    assert fake_telegram.sent[0]["buttons"] == [
        ("Review run", "https://tdt.example.com/runs/run-1")
    ]


@pytest.mark.asyncio
async def test_telegram_run_failed_includes_escaped_excerpt(
    db_session, fake_telegram
):
    await _configure_telegram(db_session)
    db_session.add(Workspace(
        business_unit_id=DEFAULT_BU_ID, id="ws-tg-2", name="db",
        repo_url="https://example.com/r.git", tf_working_dir=".",
        aws_account_id="222222222222", environment="prod",
    ))
    await db_session.commit()

    await ns.send_telegram_run_failed(
        db_session,
        workspace_id="ws-tg-2",
        workspace_name="db",
        run_id="run-2",
        command="apply",
        failed_stage="terraform apply",
        error_excerpt="Error: value <nil> & unexpected",
    )
    text = fake_telegram.sent[0]["text"]
    assert "Run failed" in text
    # The excerpt is user-controlled output; unescaped `<` would make
    # Telegram reject the whole message with "can't parse entities".
    assert "&lt;nil&gt;" in text
    assert "&amp;" in text
    assert "<pre>" in text


@pytest.mark.asyncio
async def test_telegram_auto_approved_notes_skipped_apply(db_session, fake_telegram):
    await _configure_telegram(db_session)
    db_session.add(Workspace(
        business_unit_id=DEFAULT_BU_ID, id="ws-tg-3", name="net",
        repo_url="https://example.com/r.git", tf_working_dir=".",
        aws_account_id="222222222222", environment="dev",
    ))
    await db_session.commit()

    await ns.send_telegram_run_auto_approved(
        db_session, workspace_id="ws-tg-3", workspace_name="net",
        run_id="run-3", skip_apply=True,
    )
    text = fake_telegram.sent[0]["text"]
    assert "Auto-approved" in text
    assert "apply phase skipped" in text


@pytest.mark.asyncio
async def test_telegram_drift_detected_links_the_workspace(
    db_session, fake_telegram, monkeypatch
):
    monkeypatch.setenv("PUBLIC_UI_URL", "https://tdt.example.com")
    await _configure_telegram(db_session)
    db_session.add(Workspace(
        business_unit_id=DEFAULT_BU_ID, id="ws-tg-4", name="rds",
        repo_url="https://example.com/r.git", tf_working_dir=".",
        aws_account_id="222222222222", environment="prod",
    ))
    await db_session.commit()

    await ns.send_telegram_drift_detected(
        db_session, workspace_id="ws-tg-4", workspace_name="rds",
        summary="1 resource changed outside Terraform",
    )
    text = fake_telegram.sent[0]["text"]
    assert "Drift detected" in text
    assert "https://tdt.example.com/workspaces/ws-tg-4" in text


@pytest.mark.asyncio
async def test_telegram_senders_noop_for_unknown_workspace(db_session, fake_telegram):
    await _configure_telegram(db_session)
    await ns.send_telegram_run_failed(
        db_session, workspace_id="does-not-exist", workspace_name="x",
        run_id="r", command="apply",
    )
    assert fake_telegram.sent == []
```

Add `import httpx` at the top of the test file if it is not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_notification_service.py -q -k "telegram or tg_fields"`
Expected: FAIL — `AttributeError: module 'app.services.notification_service' has no attribute 'send_telegram_bot_notification'`.

- [ ] **Step 3: Write the implementation**

In `services/api/app/services/notification_service.py`, insert after `send_slack_drift_detected` and before `send_email_notification`:

```python
# ─── Telegram bot notifications (per-BU) ───────────────────────────────────
#
# Structurally parallel to the Slack bot helpers above and sharing every
# resolution helper with them (`_resolve_bu_slug_for_workspace`,
# `_account_badge`, `_leaf_path`, `_run_link`, `_plan_summary_str`). Only the
# rendering differs: Telegram has no Block Kit and no message colour, so the
# account badge's emoji leads the message and there is no stripe.


async def _telegram_bot_creds(
    session: AsyncSession, bu_slug: str
) -> tuple[str | None, str | None]:
    from app.routers.integrations import (
        TELEGRAM_BOT_TOKEN_KEY,
        TELEGRAM_CHAT_ID_KEY,
    )

    svc = _config_svc(session)
    token = await svc.get_for_bu(bu_slug, TELEGRAM_BOT_TOKEN_KEY)
    chat_id = await svc.get_for_bu(bu_slug, TELEGRAM_CHAT_ID_KEY)
    return (token, chat_id)


def _tg_fields(pairs: list[tuple[str, str]]) -> str:
    """Render (label, value) pairs as one `<b>Label</b> value` line each.

    The Telegram counterpart of `_fields_block`. Empty / falsy values are
    skipped so we don't emit "Branch" with nothing after it. Values are
    HTML-escaped here; labels are ours and contain no markup.

    Account labels arrive carrying Slack's backtick markup (`` Name (`123`) ``),
    which means nothing in HTML mode — strip it rather than show it literally.
    """
    from app.services.telegram import _esc

    lines = []
    for label, value in pairs:
        if not value:
            continue
        lines.append(f"<b>{label}</b> {_esc(str(value).replace('`', ''))}")
    return "\n".join(lines)


async def send_telegram_bot_notification(
    session: AsyncSession,
    *,
    bu_slug: str,
    text: str,
    buttons: list[tuple[str, str]] | None = None,
) -> None:
    """Post to the BU's configured Telegram chat.

    Silently no-ops when the BU has no Telegram config. All errors are
    swallowed and logged — notification must never break the calling flow
    (run PATCH, drift detector, etc.), and must never suppress the Slack
    message sent alongside it.

    `text` is already-assembled HTML; callers escape their own values.
    """
    from app.services import telegram as tg_svc

    token, chat_id = await _telegram_bot_creds(session, bu_slug)
    if not token or not chat_id:
        return
    try:
        await tg_svc.send_message(token, chat_id, text, buttons=buttons)
    except tg_svc.TelegramError as e:
        logger.warning(
            "Telegram post failed for BU %s (chat=%s): %s %s",
            bu_slug, chat_id, e.code, e.description,
        )
    except httpx.RequestError:
        logger.warning(
            "Telegram network error for BU %s (chat=%s)",
            bu_slug, chat_id, exc_info=True,
        )


async def send_telegram_run_auto_approved(
    session: AsyncSession,
    *,
    workspace_id: str,
    workspace_name: str,
    run_id: str,
    skip_apply: bool,
    environment: str | None = None,
    region: str | None = None,
    working_dir: str | None = None,
    branch: str | None = None,
    command: str | None = None,
    triggered_by_email: str | None = None,
) -> None:
    from app.services.telegram import _esc

    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _run_link(run_id)
    leaf = _leaf_path(working_dir, region)
    badge = await _account_badge(session, workspace_id)
    head = (
        f"{badge.emoji} ✅ <b>Auto-approved (0/0/0)</b> — "
        f"<code>{_esc(workspace_name)}</code>"
    ).lstrip()
    if skip_apply:
        head += "\n<i>apply phase skipped</i>"
    body = _tg_fields(
        [
            ("Account", badge.label),
            ("Workspace", workspace_name or ""),
            ("Path", leaf),
            ("Region", region or ""),
            ("Branch", branch or ""),
            ("Command", command or ""),
            ("Triggered by", triggered_by_email or ""),
        ]
    )
    text = f'{head}\n\n{body}\n\n<a href="{_esc(link)}">View run</a>'
    await send_telegram_bot_notification(
        session, bu_slug=bu_slug, text=text, buttons=[("View run", link)]
    )


async def send_telegram_run_awaiting_approval(
    session: AsyncSession,
    *,
    workspace_id: str,
    workspace_name: str,
    run_id: str,
    environment: str | None = None,
    region: str | None = None,
    working_dir: str | None = None,
    branch: str | None = None,
    command: str | None = None,
    triggered_by_email: str | None = None,
    add: int | None = None,
    change: int | None = None,
    destroy: int | None = None,
) -> None:
    from app.services.telegram import _esc

    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _run_link(run_id)
    leaf = _leaf_path(working_dir, region)
    plan = _plan_summary_str(add, change, destroy)
    badge = await _account_badge(session, workspace_id)
    head = (
        f"{badge.emoji} ⏸ <b>Awaiting approval</b> — "
        f"<code>{_esc(workspace_name)}</code>"
    ).lstrip()
    body = _tg_fields(
        [
            ("Account", badge.label),
            ("Workspace", workspace_name or ""),
            ("Path", leaf),
            ("Region", region or ""),
            ("Branch", branch or ""),
            ("Command", command or ""),
            ("Plan", plan),
            ("Triggered by", triggered_by_email or ""),
        ]
    )
    text = f'{head}\n\n{body}\n\n<a href="{_esc(link)}">Review run</a>'
    await send_telegram_bot_notification(
        session, bu_slug=bu_slug, text=text, buttons=[("Review run", link)]
    )


async def send_telegram_run_failed(
    session: AsyncSession,
    *,
    workspace_id: str,
    workspace_name: str,
    run_id: str,
    command: str,
    environment: str | None = None,
    region: str | None = None,
    working_dir: str | None = None,
    branch: str | None = None,
    triggered_by_email: str | None = None,
    failed_stage: str | None = None,
    error_excerpt: str | None = None,
) -> None:
    from app.services.telegram import _esc

    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _run_link(run_id)
    leaf = _leaf_path(working_dir, region)
    badge = await _account_badge(session, workspace_id)
    head = (
        f"{badge.emoji} ❌ <b>Run failed</b> — "
        f"<code>{_esc(workspace_name)}</code> ({_esc(command)})"
    ).lstrip()
    body = _tg_fields(
        [
            ("Account", badge.label),
            ("Workspace", workspace_name or ""),
            ("Path", leaf),
            ("Region", region or ""),
            ("Branch", branch or ""),
            ("Failed stage", failed_stage or ""),
            ("Triggered by", triggered_by_email or ""),
        ]
    )
    text = f"{head}\n\n{body}"
    # Same 600-char cap as the Slack renderer; the excerpt is raw Terraform
    # output, so it must be escaped before it goes anywhere near parse_mode.
    excerpt = (error_excerpt or "")[:600].strip()
    if excerpt:
        text += f"\n\n<pre>{_esc(excerpt)}</pre>"
    text += f'\n\n<a href="{_esc(link)}">View run</a>'
    await send_telegram_bot_notification(
        session, bu_slug=bu_slug, text=text, buttons=[("View run", link)]
    )


async def send_telegram_drift_detected(
    session: AsyncSession,
    *,
    workspace_id: str,
    workspace_name: str,
    summary: str,
    environment: str | None = None,
    region: str | None = None,
    working_dir: str | None = None,
) -> None:
    from app.services.telegram import _esc

    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _workspace_link(workspace_id)
    leaf = _leaf_path(working_dir, region)
    badge = await _account_badge(session, workspace_id)
    head = (
        f"{badge.emoji} ⚠ <b>Drift detected</b> — "
        f"<code>{_esc(workspace_name)}</code>"
    ).lstrip()
    body = _tg_fields(
        [
            ("Account", badge.label),
            ("Workspace", workspace_name or ""),
            ("Path", leaf),
            ("Region", region or ""),
        ]
    )
    text = f"{head}\n\n{body}"
    excerpt = (summary or "")[:800].strip()
    if excerpt:
        text += f"\n\n<pre>{_esc(excerpt)}</pre>"
    text += f'\n\n<a href="{_esc(link)}">View workspace</a>'
    await send_telegram_bot_notification(
        session, bu_slug=bu_slug, text=text, buttons=[("View workspace", link)]
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_notification_service.py -q`
Expected: PASS — the pre-existing Slack tests too, unchanged.

- [ ] **Step 5: Commit**

```bash
git add services/api/app/services/notification_service.py services/api/tests/test_notification_service.py
git commit -m "feat(api): Telegram renderers for the four run/drift events

Parallel to the Slack senders and sharing every resolution helper with
them, so the two channels can never disagree about which account, path or
link an event belongs to. Telegram has no message colour, so the account
badge's emoji leads the message instead of a stripe. Terraform output is
escaped before it reaches parse_mode — an unescaped < makes Telegram
reject the whole message.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 4: Fan-out wiring

**Files:**
- Modify: `services/api/app/routers/runs.py` (the `slack_bot_events` local at line 301, its three `.append(...)` sites at ~373, ~395, ~439, and the dispatch block at ~491-513)
- Modify: `services/api/app/routers/internal.py` (~lines 99-118)
- Test: `services/api/tests/test_run_notification_fanout.py`

**Interfaces:**
- Consumes, from Task 3: `send_telegram_run_auto_approved`, `send_telegram_run_awaiting_approval`, `send_telegram_run_failed`, `send_telegram_drift_detected` — each accepting the same kwargs as its `send_slack_*` twin.
- Produces: nothing consumed by later tasks.

**Critical detail for the test:** `runs.py` imports the senders *inside* the function body, so `monkeypatch.setattr(runs_router, "send_slack_run_failed", …)` does **not** work. Patch them on the module they are imported from: `monkeypatch.setattr(notification_service, "send_slack_run_failed", …)`.

- [ ] **Step 1: Write the failing test**

Create `services/api/tests/test_run_notification_fanout.py`:

```python
"""Both notification channels fire on a run transition, independently.

A Telegram outage must not suppress the Slack message, and vice versa —
they are separate integrations and a team that configured both is relying
on each of them.

`runs.py` imports the senders inside the function body, so these tests patch
them on `app.services.notification_service`, not on the router module.
"""
import uuid

import pytest

from app.models.business_unit import DEFAULT_BU_ID
from app.services import notification_service as ns

# asyncio_mode = "auto" in pyproject.toml, so no asyncio mark is needed.
pytestmark = pytest.mark.usefixtures("default_aws_account")


async def _make_run(factory, status):
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id,
            name=f"fanout-{ws_id[:8]}",
            repo_url="https://example.com/repo.git",
            tf_working_dir=".",
            aws_account_id="123456789012",
            environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="plan",
            status=status, plan_output="example plan",
        ))
        await session.commit()
    return run_id


@pytest.fixture
def spy_senders(monkeypatch):
    """Replace both channels' senders with recorders."""
    calls = {"slack": [], "telegram": []}

    def _rec(channel, kind):
        async def _fn(session, **kw):
            calls[channel].append(kind)
        return _fn

    for kind in ("run_auto_approved", "run_awaiting_approval", "run_failed"):
        monkeypatch.setattr(ns, f"send_slack_{kind}", _rec("slack", kind))
        monkeypatch.setattr(ns, f"send_telegram_{kind}", _rec("telegram", kind))
    return calls


async def test_awaiting_approval_reaches_both_channels(
    auth_client, operator_token, _setup_db, spy_senders
):
    from app.models.run import RunStatus

    run_id = await _make_run(_setup_db, RunStatus.PLANNED)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "awaiting_approval"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert spy_senders["slack"] == ["run_awaiting_approval"]
    assert spy_senders["telegram"] == ["run_awaiting_approval"]


async def test_failed_reaches_both_channels(
    auth_client, operator_token, _setup_db, spy_senders
):
    from app.models.run import RunStatus

    run_id = await _make_run(_setup_db, RunStatus.RUNNING)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "failed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert spy_senders["slack"] == ["run_failed"]
    assert spy_senders["telegram"] == ["run_failed"]


async def test_telegram_failure_does_not_suppress_slack(
    auth_client, operator_token, _setup_db, monkeypatch
):
    """The whole point of separate try/except blocks per channel."""
    from app.models.run import RunStatus

    slack_calls = []

    async def _slack(session, **kw):
        slack_calls.append(kw["run_id"])

    async def _telegram_boom(session, **kw):
        raise RuntimeError("telegram exploded")

    monkeypatch.setattr(ns, "send_slack_run_failed", _slack)
    monkeypatch.setattr(ns, "send_telegram_run_failed", _telegram_boom)

    run_id = await _make_run(_setup_db, RunStatus.RUNNING)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "failed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert slack_calls == [run_id]


async def test_slack_failure_does_not_suppress_telegram(
    auth_client, operator_token, _setup_db, monkeypatch
):
    from app.models.run import RunStatus

    telegram_calls = []

    async def _slack_boom(session, **kw):
        raise RuntimeError("slack exploded")

    async def _telegram(session, **kw):
        telegram_calls.append(kw["run_id"])

    monkeypatch.setattr(ns, "send_slack_run_failed", _slack_boom)
    monkeypatch.setattr(ns, "send_telegram_run_failed", _telegram)

    run_id = await _make_run(_setup_db, RunStatus.RUNNING)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "failed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert telegram_calls == [run_id]


async def test_internal_drift_report_reaches_both_channels(
    auth_client, _setup_db, monkeypatch
):
    """The /internal/ router authenticates with TERRADUCKTEL_INTERNAL_TOKEN,
    NOT a user JWT — `tests/conftest.py` sets it to the constant below, and
    `DriftReportIn` requires `workspace_id` in the body to match the path."""
    from app.models.workspace import Workspace

    calls = []

    async def _slack(session, **kw):
        calls.append("slack")

    async def _telegram(session, **kw):
        calls.append("telegram")

    monkeypatch.setattr(ns, "send_slack_drift_detected", _slack)
    monkeypatch.setattr(ns, "send_telegram_drift_detected", _telegram)

    ws_id = str(uuid.uuid4())
    async with _setup_db() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID, id=ws_id, name=f"drift-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        await session.commit()

    r = await auth_client.post(
        f"/api/v1/internal/drift/{ws_id}/report",
        json={"workspace_id": ws_id, "has_drift": True, "summary": "1 changed"},
        headers={
            "X-Terraducktel-Internal-Token": "test-internal-token-do-not-use-in-prod"
        },
    )
    assert r.status_code == 200
    assert sorted(calls) == ["slack", "telegram"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_run_notification_fanout.py -q`
Expected: FAIL — `AttributeError` on `send_telegram_run_awaiting_approval` during monkeypatch setup, or the telegram spy list staying empty.

- [ ] **Step 3: Rename the events list in `runs.py`**

Rename the local `slack_bot_events` to `bot_events` at all five sites (line 301 declaration, three `.append(` calls, and the `if slack_bot_events:` guard). It is a function-local; nothing outside reads it.

```python
# line 301
bot_events: list[tuple[str, dict]] = []
```

- [ ] **Step 4: Dispatch to both channels in `runs.py`**

Replace the dispatch block (~line 491, previously `if slack_bot_events:`) with:

```python
    # Bot-channel dispatch — separate session so it can't roll back the FSM
    # transition. Each channel is wrapped on its own: a Telegram outage must
    # not suppress the Slack message, and vice versa.
    if bot_events:
        from app.services.notification_service import (
            send_slack_run_auto_approved,
            send_slack_run_awaiting_approval,
            send_slack_run_failed,
            send_telegram_run_auto_approved,
            send_telegram_run_awaiting_approval,
            send_telegram_run_failed,
        )

        senders = {
            "auto_approved": (
                ("slack", send_slack_run_auto_approved),
                ("telegram", send_telegram_run_auto_approved),
            ),
            "awaiting_approval": (
                ("slack", send_slack_run_awaiting_approval),
                ("telegram", send_telegram_run_awaiting_approval),
            ),
            "failed": (
                ("slack", send_slack_run_failed),
                ("telegram", send_telegram_run_failed),
            ),
        }

        async with _db.AsyncSessionLocal() as ns_session:
            for kind, payload in bot_events:
                for channel, send in senders.get(kind, ()):
                    try:
                        await send(ns_session, **payload)
                    except Exception:  # noqa: BLE001 — best-effort
                        logger.warning(
                            "%s notification (%s) failed for run %s",
                            channel, kind, run.id, exc_info=True,
                        )
```

> The senders are looked up through the module at call time via the
> `from … import` above, which binds the function objects once. That is fine
> for production but means the fan-out test must patch
> `app.services.notification_service` **before** the request is made — which it
> does, because the import runs inside the request handler.

- [ ] **Step 5: Add the Telegram drift call in `internal.py`**

In `services/api/app/routers/internal.py`, replace the `if body.has_drift:` block (~line 99) with:

```python
    # Bot-channel notification on transition into drifted state. Best-effort —
    # the report is already committed, so an outage cannot lose the drift
    # record. Each channel is wrapped separately so one failing does not
    # suppress the other.
    if body.has_drift:
        from app.services.notification_service import (
            send_slack_drift_detected,
            send_telegram_drift_detected,
        )

        for channel, send in (
            ("slack", send_slack_drift_detected),
            ("telegram", send_telegram_drift_detected),
        ):
            try:
                await send(
                    db,
                    workspace_id=workspace_id,
                    workspace_name=ws.name,
                    summary=body.summary or "",
                    environment=ws.environment,
                    region=ws.region,
                    working_dir=ws.tf_working_dir,
                )
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning(
                    "%s drift notification failed for workspace %s",
                    channel, workspace_id, exc_info=True,
                )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_run_notification_fanout.py tests/test_patch_run_notification_resilience.py tests/test_approval_flow.py -q`
Expected: PASS — including the pre-existing resilience and approval tests.

- [ ] **Step 7: Run the full API suite**

Run: `make test-api`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add services/api/app/routers/runs.py services/api/app/routers/internal.py services/api/tests/test_run_notification_fanout.py
git commit -m "feat(api): fan run and drift events out to Slack and Telegram

Each channel gets its own try/except: a team that configured both is
relying on both, so one integration's outage must not swallow the other's
message. slack_bot_events is renamed bot_events now that it feeds more
than one channel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 5: Settings UI

**Files:**
- Modify: `services/ui/src/pages/Settings.tsx` — add `ICON.telegram` to the icon registry (~line 2244, beside `ICON.slack`); add the `TelegramSection` component after `SlackSection` ends (~line 1635, just before the `// ─── security (checkov gate) ───` comment); add the tab entry to `allTabs` (~line 2267, directly after the `slack` row).

**Interfaces:**
- Consumes, from Task 2: the five endpoint shapes listed in that task's Produces block.
- Consumes, existing in this file: `api` (axios instance), `Card`, `CardHeader`, `CardTitle`, `CardBody`, `Badge`, `Button`, `Input`, `Label`, `Field`, `Spinner`, `ConfirmDialog`, `ScopeBadge`, `Icon`, `ICON`, `hasMinRole`, `UserRole`. Follow `SlackSection` (line 1269) for every prop signature — do not invent new shared components.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the icon**

In the `ICON` registry, directly after the `slack:` entry, add a paper-plane path on the same 24×24 viewBox the other icons use:

```tsx
  telegram: <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z" />,
```

- [ ] **Step 2: Add the `TelegramSection` component**

Insert after the closing brace of `SlackSection`:

```tsx
// ─── Telegram (bot token + chat) ───────────────────────────────────────────

type TelegramStatus = {
  configured: boolean;
  token_tail?: string | null;
  bot_username?: string | null;
  chat_id?: string | null;
  chat_title?: string | null;
};

type TelegramTestResult = {
  ok: boolean;
  detail?: string;
  bot_username?: string;
  chat_title?: string;
};

function TelegramSection() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [test, setTest] = useState<TelegramTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [sending, setSending] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/telegram");
      setStatus(r.data);
      setChatInput(r.data?.chat_id ?? "");
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function saveToken(e: FormEvent) {
    e.preventDefault();
    if (!tokenInput) return;
    setSubmitting(true);
    setError(null);
    setTest(null);
    try {
      await api.put("/v1/integrations/telegram", { token: tokenInput });
      setTokenInput("");
      setEditing(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function saveChat() {
    if (!chatInput) return;
    setSubmitting(true);
    setError(null);
    setTest(null);
    try {
      await api.put("/v1/integrations/telegram", { chat_id: chatInput.trim() });
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function remove() {
    setSubmitting(true);
    setError(null);
    try {
      await api.delete("/v1/integrations/telegram");
      setChatInput("");
      setTest(null);
      setConfirmRemove(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Remove failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    try {
      const r = await api.post("/v1/integrations/telegram/test");
      setTest(r.data);
    } catch (e: any) {
      setTest({ ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  async function sendTestMessage() {
    setSending(true);
    setTest(null);
    try {
      const r = await api.post("/v1/integrations/telegram/test-message");
      setTest(r.data);
    } catch (e: any) {
      setTest({ ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Send failed" });
    } finally {
      setSending(false);
    }
  }

  return (
    <div>
      <ScopeBadge />
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-emerald-600 dark:text-emerald-400">
              {ICON.telegram}
            </svg>
            <CardTitle>Telegram notifications</CardTitle>
          </div>
          {status?.configured && status.chat_id && <Badge tone="success">configured</Badge>}
          {status?.configured && !status.chat_id && <Badge tone="warning">set a chat</Badge>}
          {!status?.configured && <Badge tone="neutral">not set</Badge>}
        </CardHeader>
        <CardBody className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Bot token + a chat id for run + drift notifications. We post on{" "}
            <strong>auto-approved (0/0/0)</strong>, <strong>awaiting approval</strong>,{" "}
            <strong>run failed</strong>, and <strong>drift detected</strong> — the same
            four events as Slack, and both fire independently when both are configured.
            The token is encrypted at rest and never returned.
          </p>

          {loading ? (
            <p className="text-sm italic text-slate-500">Loading…</p>
          ) : status?.configured ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Token" value={<span className="font-mono">{status.token_tail ?? "configured"}</span>} />
              <Field label="Bot" value={status.bot_username ? `@${status.bot_username}` : "—"} />
              <Field
                label="Chat"
                value={
                  status.chat_id ? (
                    <span>
                      {status.chat_title ? <strong>{status.chat_title}</strong> : null}{" "}
                      <span className="font-mono text-xs text-slate-500">{status.chat_id}</span>
                    </span>
                  ) : (
                    <span className="text-amber-600">not set yet</span>
                  )
                }
              />
            </div>
          ) : null}

          {test && (
            <div
              className={
                "rounded-md border px-3 py-2 text-xs " +
                (test.ok
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300"
                  : "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300")
              }
            >
              {test.ok ? (
                <p>
                  ✓ {test.detail ?? "Connected"}
                  {test.bot_username && <> · bot <code className="font-mono">@{test.bot_username}</code></>}
                  {test.chat_title && <> · chat <strong>{test.chat_title}</strong></>}
                </p>
              ) : (
                <p>✕ {test.detail ?? "Test failed"}</p>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}

          {editing ? (
            <form onSubmit={saveToken} className="space-y-3">
              <div>
                <Label htmlFor="telegram-token">Bot token</Label>
                <Input
                  id="telegram-token"
                  type="password"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="123456789:AA…"
                  autoComplete="off"
                  required
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Saving verifies the token against{" "}
                  <code className="font-mono">getMe</code> — rejected tokens never get
                  persisted.
                </p>
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={submitting || !tokenInput}>
                  {submitting ? <><Spinner /> Verifying…</> : "Save token"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setEditing(false);
                    setTokenInput("");
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={() => setEditing(true)}>
                {status?.configured ? "Replace token" : "Add token"}
              </Button>
              {status?.configured && (
                <Button type="button" variant="secondary" onClick={runTest} disabled={testing}>
                  {testing ? <><Spinner /> Testing…</> : "Test connection"}
                </Button>
              )}
              {status?.configured && status.chat_id && (
                <Button type="button" variant="secondary" onClick={sendTestMessage} disabled={sending}>
                  {sending ? <><Spinner /> Sending…</> : "Send test message"}
                </Button>
              )}
              {status?.configured && (
                <Button type="button" variant="ghost" onClick={() => setConfirmRemove(true)}>
                  Remove
                </Button>
              )}
            </div>
          )}

          {status?.configured && (
            <div className="space-y-2 border-t border-slate-200 pt-4 dark:border-slate-700">
              <Label htmlFor="telegram-chat">Chat id</Label>
              <div className="flex flex-wrap gap-2">
                <Input
                  id="telegram-chat"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  placeholder="-1001234567890 or @my_channel"
                  autoComplete="off"
                  className="max-w-xs font-mono"
                />
                <Button
                  type="button"
                  onClick={saveChat}
                  disabled={submitting || !chatInput || chatInput === status.chat_id}
                >
                  {submitting ? <><Spinner /> Verifying…</> : "Verify & save chat"}
                </Button>
              </div>
              <p className="text-[11px] text-slate-500">
                Saving resolves the chat with <code className="font-mono">getChat</code>.
                That proves the bot can <em>see</em> the chat — in a channel it also needs
                the <strong>Post messages</strong> admin right, which only{" "}
                <strong>Send test message</strong> can confirm.
              </p>
              <button
                type="button"
                className="text-[11px] underline text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                onClick={() => setShowHelp((v) => !v)}
              >
                {showHelp ? "Hide setup steps" : "How do I get a token and chat id?"}
              </button>
              {showHelp && (
                <ol className="ml-4 list-decimal space-y-1 text-[11px] text-slate-500">
                  <li>
                    Message <code className="font-mono">@BotFather</code> on Telegram,
                    send <code className="font-mono">/newbot</code>, and copy the token
                    it gives you.
                  </li>
                  <li>
                    Add the bot to your group, or add it to a channel as an administrator
                    with the <strong>Post messages</strong> right.
                  </li>
                  <li>
                    For a public channel, the chat id is just{" "}
                    <code className="font-mono">@channelusername</code>. For a private
                    group, send a message in the group and open{" "}
                    <code className="font-mono">
                      https://api.telegram.org/bot&lt;token&gt;/getUpdates
                    </code>{" "}
                    — the numeric <code className="font-mono">chat.id</code> is in the
                    response, and starts with <code className="font-mono">-100</code> for
                    a supergroup.
                  </li>
                </ol>
              )}
            </div>
          )}
        </CardBody>
      </Card>
      <ConfirmDialog
        open={confirmRemove}
        tone="danger"
        title="Remove Telegram integration"
        message={
          <>
            Remove the Telegram integration? Future run / drift notifications will stop
            going to this chat until a new token is set. Slack, if configured, is
            unaffected.
          </>
        }
        confirmLabel="Remove"
        busy={submitting}
        onConfirm={remove}
        onCancel={() => setConfirmRemove(false)}
      />
    </div>
  );
}
```

- [ ] **Step 3: Register the tab**

In `allTabs`, directly after the `slack` row:

```tsx
    { id: "telegram", label: "Telegram", icon: <Icon d={ICON.telegram} />, roleGate: "admin" as UserRole, render: () => <TelegramSection /> },
```

- [ ] **Step 4: Typecheck and build**

Run: `cd services/ui && npm run build`
Expected: PASS — no TypeScript errors.

If the repo exposes a lint script, also run: `cd services/ui && npm run lint`

- [ ] **Step 5: Run the UI test suite**

Run: `make test-ui`
Expected: PASS — no existing test asserts on the tab list, but run it to be sure.

- [ ] **Step 6: Commit**

```bash
git add services/ui/src/pages/Settings.tsx
git commit -m "feat(ui): Telegram settings section

Token then chat, each verified on save, because a Telegram bot cannot list
its chats and there is no dropdown to pick from. The test-message button is
the only way to prove the bot may actually post: getChat succeeds even in a
channel where the bot lacks the Post messages right.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/API.md` (integrations section ~line 932, run-notification note ~line 706, drift note ~line 743, error codes ~line 1044)
- Modify: `docs/ARCHITECTURE.md` (notifications section ~line 563)
- Modify: `README.md` (wherever Slack is listed as an integration)

**Interfaces:**
- Consumes: the endpoint list from Task 2 and the behaviour from Tasks 3-4. Verify each documented path against the implemented router before writing.
- Produces: nothing.

- [ ] **Step 1: Add the Telegram endpoint table to `docs/API.md`**

Directly after the existing `### Slack (bot token + channel)` table, add:

```markdown
### Telegram (bot token + chat)

Per-BU, admin-only, same storage and masking rules as Slack. The bot token is
encrypted at rest and never returned.

| Method | Path | Notes |
|---|---|---|
| GET | `/integrations/telegram` | `{configured, token_tail, bot_username, chat_id, chat_title}` |
| PUT | `/integrations/telegram` | Body: `{token?, chat_id?}`. Verifies the token via `getMe` before persisting; 422 if no token is saved yet and none is supplied, or if `chat_id` is neither numeric nor a public `@username`. When `chat_id` is given it is resolved with `getChat` and the title cached. |
| DELETE | `/integrations/telegram` | Remove the bot token + chat. |
| POST | `/integrations/telegram/test` | Re-verify the saved token and re-read the chat. Returns `{ok, bot_username, chat_title, detail}`. |
| POST | `/integrations/telegram/test-message` | Post a confirmation message to the configured chat. Returns `{ok, detail}`. |

There is no channel-listing endpoint: the Telegram Bot API gives a bot no way
to enumerate the chats it belongs to. `getChat` proves the bot can see a chat
but not that it may post there — in a channel the bot must be an administrator
with the "Post messages" right — which is why `test-message` exists.
```

- [ ] **Step 2: Update the run-notification and drift notes in `docs/API.md`**

At ~line 706, change "Posts a Slack notification if the BU has Slack integration configured." to:

```markdown
4. Posts a Slack and/or Telegram notification, to whichever of the two the BU
   has configured. Both fire independently; one channel's outage never
   suppresses the other.
```

At ~line 709-710, change "All four Slack notification kinds …" to:

```markdown
approve). All four bot notification kinds (auto-approved, awaiting approval,
run failed, drift detected) require the BU to have a Slack and/or Telegram
integration configured
```

At ~line 743, change "trigger a Slack alert whenever `has_drift=true`" to
"trigger a Slack and/or Telegram alert whenever `has_drift=true`".

- [ ] **Step 3: Update the error codes in `docs/API.md`**

At ~line 1044, extend the `400` line to mention Telegram, and at ~line 1050
extend the `502` line's integration list:

```markdown
- `400` — bad input (e.g. invalid Slack token, invalid Telegram token or chat, channel id missing, BU scoped to "all" where a concrete BU is required).
```
```markdown
- `502` — upstream integration unreachable (GitHub, Slack, Telegram, Infracost).
```

- [ ] **Step 4: Update `docs/ARCHITECTURE.md`**

In the notifications section (~line 563), after the Slack bullet, add:

```markdown
- **Telegram** — a per-BU bot token + chat id (`telegram.bot_token`,
  `telegram.chat_id`, `services/api/app/services/telegram.py`), carrying the
  same four run / drift events as the Slack bot path. Messages use the Bot
  API's HTML parse mode; there is no Block Kit equivalent, so the cloud
  account's colour appears as a leading emoji rather than a stripe, and links
  render as inline URL buttons (dropped when the public base URL is not
  https, which Telegram rejects). Telegram and Slack are independent: a BU may
  configure either, both, or neither, and each send is wrapped separately so
  one channel's outage cannot suppress the other. There is no inbound
  handling — buttons link into the UI, they do not approve runs.
```

- [ ] **Step 5: Update `README.md`**

Slack appears exactly once, in the encrypted-credentials bullet (~line 76).
Change that bullet's parenthetical to name the Telegram token too:

```markdown
- **Encrypted credentials at rest** — AWS access/secret pairs and integration
  tokens (GitHub PAT, Slack bot token, Telegram bot token, SMTP password) are
  stored Fernet-encrypted with an HKDF-derived key. Plaintext never leaves the
  request handler.
```

The feature list has no notifications bullet at all. Add one directly after
the **Drift detection** bullet:

```markdown
- **Run + drift notifications** — Slack and/or Telegram per Business Unit, plus
  SMTP email. Both chat channels carry the same four events (auto-approved,
  awaiting approval, run failed, drift detected) and fire independently.
```

- [ ] **Step 6: Verify the documented paths against the code**

Run: `cd services/api && python -c "
from app.main import app
paths = sorted(r.path for r in app.routes if 'telegram' in getattr(r, 'path', ''))
print('\n'.join(paths))
"`
Expected output — exactly these five, matching the table written in Step 1:
```
/api/v1/integrations/telegram
/api/v1/integrations/telegram
/api/v1/integrations/telegram
/api/v1/integrations/telegram/test
/api/v1/integrations/telegram/test-message
```
The bare path appears three times — one route object each for GET, PUT and
DELETE. Five lines total; any other count means a route is missing or a path
was mistyped.

- [ ] **Step 7: Commit**

```bash
git add docs/API.md docs/ARCHITECTURE.md README.md
git commit -m "docs: Telegram as a second bot notification channel

Records why there is no channel-listing endpoint and why test-message
exists, so the asymmetry with the Slack section does not read as an
oversight to the next person.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

## Final Verification

- [ ] `make test-api` — full API suite green
- [ ] `make test-ui` — UI suite green
- [ ] `cd services/ui && npm run build` — production build clean
- [ ] `git log --oneline main..HEAD` — six feature commits plus the spec commit
- [ ] `grep -rn "telegram.bot_token" services/api/app/routers/ | grep -v "TELEGRAM_BOT_TOKEN_KEY"` — returns nothing, i.e. the key is referenced only through its constant
- [ ] Confirm no endpoint returns the token: `cd services/api && python -m pytest tests/test_telegram_integration_api.py -q -k "never_returns or scoped"`
