# Telegram notification support — design

**Date:** 2026-09-13
**Status:** approved design, pending implementation plan
**Branch:** `telegram_support` (off `main` @ `f3b1bad`)

## Problem

Terraducktel notifies operators about run and drift events through Slack (a
per-BU bot token + channel) and SMTP email. Teams that run on Telegram have no
way to receive those events. They want the same four notifications — run
auto-approved, run awaiting approval, run failed, drift detected — delivered to
a Telegram group or channel, configured per Business Unit with the same RBAC,
encryption and audit behaviour as the Slack integration.

## Decision summary

Add Telegram as a second bot-based notification channel, structurally parallel
to Slack: an HTTP wrapper service, per-BU encrypted config keys, admin-gated
integration endpoints, a Settings section, and four senders fanned out beside
the Slack senders.

Rejected alternative: refactoring `notification_service.py` into a
channel-agnostic event + per-channel renderers. Cleaner for a hypothetical
third channel, but it rewrites a working, well-tested Slack path for no
present gain. Revisit if a third channel ever lands.

Decisions taken during brainstorming:

| Question | Decision |
|---|---|
| Notification layer shape | Parallel `send_telegram_*` functions. The Slack path is not modified beyond adding the fan-out call. |
| Chat selection | Operator enters the chat ID manually. `PUT` verifies it with `getChat` and caches the title. A bot cannot enumerate its own chats, so there is no dropdown. |
| Slack + Telegram both configured | Both fire, independently. Each send is wrapped separately so one channel's outage cannot suppress the other. |
| Message format | HTML `parse_mode`, not MarkdownV2. |
| Inbound Telegram (approve from a button) | Out of scope. Buttons are URL links into the UI, exactly like Slack's. |

## 1. Service wrapper — `services/api/app/services/telegram.py`

Mirrors `services/api/app/services/slack.py`: async, `httpx`, 10s timeout, no
database access. All calls go to `https://api.telegram.org/bot{token}/{method}`.

Telegram's envelope is `{"ok": true, "result": …}` on success and
`{"ok": false, "error_code": 400, "description": "Bad Request: chat not found"}`
on failure — the same success-flag shape `slack.py` already handles.

```python
class TelegramError(Exception):
    """Telegram returned ok=false. Carries error_code as `code` and the
    description as `description`."""
    code: int
    description: str

@dataclass(frozen=True)
class TelegramIdentity:   # getMe
    bot_id: int
    username: str          # without the leading @
    first_name: str

@dataclass(frozen=True)
class TelegramChat:        # getChat
    id: str
    title: str             # `title` for group/supergroup/channel,
                           # `first_name` for a private chat
    type: str              # private | group | supergroup | channel

async def verify_token(token: str) -> TelegramIdentity
async def get_chat(token: str, chat_id: str) -> TelegramChat
async def send_message(token: str, chat_id: str, text: str,
                       buttons: list[tuple[str, str]] | None = None) -> None
```

### Why HTML and not MarkdownV2

Telegram's MarkdownV2 requires every one of ``_ * [ ] ( ) ~ ` > # + - = | { } . !``
to be backslash-escaped *anywhere* it appears, including inside literal content.
Workspace names, branch names, leaf paths and Terraform plan excerpts are full
of `-`, `.` and `_`. HTML `parse_mode` needs only `&`, `<` and `>` escaped and
supports `<b>`, `<i>`, `<code>`, `<pre>` and `<a href>` — everything the Slack
Block Kit messages express. One escape helper, one rule, far less to get wrong.

A module-level `_esc(s)` escapes `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;` in
that order. Every interpolated value passes through it; callers assemble
already-escaped HTML.

### Inline buttons require https

`buttons` renders as
`reply_markup: {"inline_keyboard": [[{"text": t, "url": u}, …]]}`.

Telegram rejects an inline URL button whose URL is not `https://` (or `tg://`)
with `Bad Request: inline keyboard button url is invalid`. TDT's own links come
from `PUBLIC_UI_URL` / `PUBLIC_API_URL`, which in development default to
`http://localhost:8000`. So `send_message` drops the keyboard when any button
URL is not https, rather than failing the send. The senders always also embed
the link inline in the message body (`<a href="…">view run</a>`), so the
message stays useful either way — the same reasoning behind the Slack senders
keeping `<url|text>` in `text` alongside the actions block.

### Length guard

`sendMessage` hard-caps `text` at 4096 characters; Slack has no comparable
limit, so this is new behaviour with no Slack counterpart to copy.

Every variable-length field is already capped at the source (error excerpt 600
chars, drift summary 800 — the existing Slack limits, reused). The guard in
`send_message` is therefore a safety net that should never fire in practice:
when `len(text) > 4096`, cut at the last newline before 3990 characters, append
`\n…(truncated)`, then close any tag left open by the cut. A small
`_close_open_tags(html)` helper scans for unbalanced `<b> <i> <code> <pre> <a>`
and appends the matching closers in reverse order — truncating mid-tag would
make Telegram reject the whole message with `can't parse entities`.

Link previews are disabled via `link_preview_options: {"is_disabled": true}`.

### Error handling

`TelegramError` carries the numeric `error_code` so callers can distinguish:

| Code | Meaning | Caller behaviour |
|---|---|---|
| 401 | token revoked or malformed | `PUT`/`test` → 400 to the operator; senders log a warning |
| 400 | `chat not found`, bad entities, bad button URL | same |
| 403 | bot blocked, kicked from the group, or not a channel admin | same |
| 429 | rate limited; `parameters.retry_after` seconds | senders log and drop — never sleep-and-retry inside a run transition |

Telegram's per-chat limit is roughly 20 messages per minute; TDT's event volume
is far below that, so no client-side throttle is built. Network failures raise
`httpx.RequestError` and bubble up, matching `slack.py`.

## 2. Configuration

Four config keys, all per-BU through `ConfigService.set_for_bu` /
`get_for_bu` (namespaced `bu.<slug>.*`), declared beside the `SLACK_*_KEY`
constants in `routers/integrations.py`:

| Key | Secret | Contents |
|---|---|---|
| `telegram.bot_token` | yes | `<bot_id>:<secret>` from @BotFather |
| `telegram.chat_id` | no | `-1001234567890`, `12345678`, or `@channelname` |
| `telegram.chat_title` | no | cached from `getChat`, for display only |
| `telegram.bot_username` | no | cached from `getMe`, for display only |

Chat IDs are validated on write against `^(-?\d+|@[A-Za-z][A-Za-z0-9_]{4,31})$`
— numeric (negative for groups and supergroups) or an `@username` for a public
channel.

No new environment variables and no migration: these are rows in the existing
encrypted `config` table.

## 3. API surface

Five endpoints in `services/api/app/routers/integrations.py`, all
`require_role(Role.admin)` and BU-scoped through `_require_bu(bu)`, reusing the
existing `_config_svc` and `_mask_tail` helpers.

| Method | Path | Behaviour |
|---|---|---|
| GET | `/api/v1/integrations/telegram` | `{configured, token_tail, bot_username, chat_id, chat_title}`. Never returns the token. |
| PUT | `/api/v1/integrations/telegram` | Body `{token?, chat_id?}`. Token required on first save (422 otherwise), reused from storage on later saves. Verifies with `getMe` (400 on rejection, 502 on network error) and caches `bot_username`. When `chat_id` is supplied, validates the format (422), calls `getChat` (400 if the bot cannot see it) and caches `chat_title`. |
| DELETE | `/api/v1/integrations/telegram` | Deletes all four keys. 204. |
| POST | `/api/v1/integrations/telegram/test` | Re-verifies the saved token and re-reads the chat. `{ok, bot_username, chat_title, detail}`. 400 when nothing is configured. |
| POST | `/api/v1/integrations/telegram/test-message` | Posts "✅ Terraducktel is connected to this chat." to the configured chat. `{ok, detail}`. 400 when token or chat is unset. |

There is no `/telegram/chats` endpoint. The Bot API offers no way to list the
chats a bot belongs to; `getUpdates` only surfaces chats that messaged the bot
within the last 24 hours, requires group privacy mode to be off, and returns
`409 Conflict` whenever a webhook is registered. A dropdown built on it would
be wrong often enough to mislead.

`test-message` has **no Slack counterpart and is a deliberate addition.**
Without a channel picker, `getChat` succeeding is the only signal an operator
gets — and it does not prove the bot may *post*: in a channel the bot must be
an administrator, and `getChat` succeeds regardless. Actually sending a message
is the only honest confirmation that the integration works.

## 4. Notification senders

Four functions in `services/api/app/services/notification_service.py`,
positioned directly after their Slack counterparts, over one shared primitive:

```python
async def send_telegram_bot_notification(
    session, *, bu_slug: str, text: str,
    buttons: list[tuple[str, str]] | None = None,
) -> None
```

It reads the BU's token and chat id, returns silently when either is unset, and
swallows `TelegramError` and `httpx.RequestError` with a warning — identical
contract to `send_slack_bot_notification`.

```python
send_telegram_run_auto_approved(...)
send_telegram_run_awaiting_approval(...)
send_telegram_run_failed(...)
send_telegram_drift_detected(...)
```

Each takes the same keyword arguments as its Slack twin, so `runs.py` can pass
one payload dict to both.

These reuse the existing helpers **unchanged**: `_resolve_bu_slug_for_workspace`,
`_account_badge`, `_leaf_path`, `_run_link`, `_workspace_link`,
`_plan_summary_str`. Only rendering is new — a `_tg_fields(pairs)` helper
emitting one `<b>Label</b> value` line per non-empty pair, replacing
`_fields_block`.

The account badge's `hex` has no Telegram equivalent: there is no coloured
stripe and no message-level colour in the Bot API. The badge's existing
`emoji` leads the message instead, which is already how the Slack fallback
`text` is built.

Message shape, using "awaiting approval" as the example:

```
🟦 ⏸ <b>Awaiting approval</b> — <code>prod-vpc</code>

<b>Account</b> Prod-Account (444444444444)
<b>Path</b> relprod/ai-cog
<b>Region</b> us-east-1
<b>Branch</b> main
<b>Plan</b> +3 ~1 −0
<b>Triggered by</b> operator@test.com

<a href="https://tdt.example.com/runs/abc123">Review run</a>
```

with a single inline button `Review run` when the base URL is https.

## 5. Fan-out

**`services/api/app/routers/runs.py`** — the `slack_bot_events` list is renamed
`bot_events` (it is local to the function; nothing outside reads it). The
existing dispatch loop gains a Telegram call per kind, each in its own
`try/except Exception` so a Telegram failure cannot suppress the Slack message
that follows or precedes it. One shared `AsyncSessionLocal()` session, as today.

**`services/api/app/routers/internal.py`** — `send_telegram_drift_detected` is
called beside `send_slack_drift_detected`, inside its own best-effort wrapper.

### What is deliberately left alone

`send_plan_approval_notification` and `send_drift_alert` use the global,
non-BU `slack.webhook_url` key and build Slack incoming-webhook payloads. They
stay Slack-only: that path predates Business Units and has no Telegram
analogue. This creates no coverage gap, because the per-BU bot path already
fires on the same two events and that is the path Telegram joins.

**Known pre-existing asymmetry, out of scope:** the user-facing
`POST /drift/{workspace_id}/report` in `drift.py` calls only `send_drift_alert`
(legacy webhook + email), while the drift-detector's
`POST /internal/drift/{workspace_id}/report` additionally calls
`send_slack_drift_detected`. Telegram follows Slack exactly and is therefore
wired into `internal.py` only. Making both routes behave alike would change
Slack's behaviour, which this work explicitly does not touch.

## 6. UI

A `TelegramSection` component in `services/ui/src/pages/Settings.tsx`, modelled
on `SlackSection`, and a new tab registered in `allTabs` between `slack` and
`webhook`:

```tsx
{ id: "telegram", label: "Telegram", icon: <Icon d={ICON.telegram} />,
  roleGate: "admin" as UserRole, render: () => <TelegramSection /> },
```

with a `telegram` entry added to the `ICON` registry (paper-plane path, sized
to the existing 24×24 viewBox).

Flow, mirroring the Slack card's states:

1. **Not configured** — bot token input, "Save & verify" button. Errors from
   `getMe` surface inline.
2. **Token saved** — shows `@botusername` and the masked token tail. Chat ID
   input with a "Verify chat" action; on success shows the resolved chat title
   and type.
3. **Fully configured** — "Send test message" button and a "Remove integration"
   action behind the same confirmation dialog pattern Slack uses.

A collapsed setup hint covers what the Slack dropdown makes unnecessary:
create a bot with @BotFather, add it to the group (or make it a channel
administrator with "Post messages"), and find the chat ID. No Tailwind `sky-*`
colours; `brand-*` / `accent-*` only.

## 7. Testing

**`services/api/tests/test_telegram_service.py`** (new) — the wrapper, with
`httpx.AsyncClient` monkeypatched, no network:

- `verify_token` parses `getMe`; `ok:false` raises `TelegramError` with the
  numeric code preserved.
- `get_chat` maps `title` for a supergroup and falls back to `first_name` for a
  private chat.
- `_esc` escapes `&`, `<`, `>` and in the right order (`&` first, so `<` does
  not become `&amp;lt;`).
- `send_message` sends `parse_mode: "HTML"` and disabled link previews.
- Buttons render as `inline_keyboard`; a non-https button URL drops
  `reply_markup` entirely while the text is still sent.
- A >4096-character message is truncated below the cap, ends with the truncation
  marker, and leaves no unclosed tag (`_close_open_tags` covers nesting).
- 429 surfaces as `TelegramError(code=429)`.

**`services/api/tests/test_notification_service.py`** (extend) — the four
senders, following the existing `_FakeHttpClient` fixture pattern:

- Each is a silent no-op when the BU has no token or no chat id.
- Each posts to the configured chat with the expected title line, the account
  badge emoji, and the run link.
- A `TelegramError` and an `httpx.RequestError` are both swallowed, not raised.
- A configured Telegram BU and a configured Slack BU each receive their own
  message, and a Telegram failure does not prevent the Slack send.

**Endpoint tests** (new `services/api/tests/test_telegram_integration_api.py`)
— the five endpoints against the existing async client fixtures:

- `viewer` and `operator` get 403; `admin` succeeds.
- `GET` never includes the token in the response body.
- `PUT` with no stored token and no supplied token → 422.
- `PUT` with a token Telegram rejects → 400; unreachable → 502.
- `PUT` with a malformed chat id → 422, before any network call.
- Config is BU-scoped: a token saved under BU `a` is invisible to BU `b`.
- `POST /telegram/test` and `POST /telegram/test-message` both return 400 when
  nothing is configured, and `test-message` sends to the stored chat id when it
  is.
- `DELETE` removes all four keys.

Run with `make test-api`. `make test-ui` covers the UI build.

## 8. Documentation

- `docs/API.md` — a "Telegram (bot token + chat)" subsection beside the Slack
  one in the integrations table; note Telegram in the run-notification and
  drift sections; add Telegram to the 400/502 error-code descriptions.
- `docs/ARCHITECTURE.md` — extend the notifications section: Telegram is the
  third delivery channel alongside Slack and SMTP, per-BU, best-effort, HTML
  parse mode, no inbound handling.
- `README.md` — add Telegram wherever Slack is listed as a supported
  integration.
- `CLAUDE.md` needs no change: no new env var, no new table, no new convention.

## Out of scope

- **Inbound Telegram.** No approve/reject from a Telegram button. That needs a
  publicly reachable webhook endpoint, `callback_query` handling, replay
  protection, and a Telegram-user → TDT-user identity map with its own RBAC
  story. Buttons are links into the UI, exactly as Slack's are.
- **Forum topic threading** (`message_thread_id`) — a single extra optional
  field if it is ever wanted; not built now.
- **Per-workspace or per-event routing.** One chat per BU, matching Slack's one
  channel per BU.
- **Refactoring the Slack senders** into a shared renderer abstraction.
- **Telegram for the legacy `slack.webhook_url` path** and for the user-facing
  `drift.py` report route, per §5.
