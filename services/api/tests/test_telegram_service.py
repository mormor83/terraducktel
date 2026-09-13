"""Unit coverage for services/telegram.py — the Telegram Bot API wrapper.

httpx.AsyncClient is monkeypatched; no test here touches the network.
"""
import json

import httpx
import pytest

from app.services import telegram as tg

pytestmark = pytest.mark.asyncio


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
