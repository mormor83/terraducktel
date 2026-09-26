"""Unit coverage for notification_service: Slack webhook + bot, generic webhook,
SMTP email, drift alerts, run-event blocks, and the link/leaf helpers.
httpx.AsyncClient and smtplib.SMTP are mocked — no real network."""
import httpx
import pytest

from app.services import notification_service as ns
from app.services import slack as slack_svc
from app.services.config_service import ConfigService
from app.auth.encryption_key import get_credential_encryption_key
from app.models.workspace import Workspace
from app.models.business_unit import DEFAULT_BU_ID

pytestmark = pytest.mark.usefixtures("default_bu")


# ─── httpx mock ──────────────────────────────────────────────────────────────


class _FakeHttpClient:
    last_posts: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, timeout=None):
        _FakeHttpClient.last_posts.append((url, json))
        if "boom" in (url or ""):
            import httpx

            raise httpx.RequestError("down", request=None)
        return None


@pytest.fixture
def fake_http(monkeypatch):
    _FakeHttpClient.last_posts = []
    monkeypatch.setattr(ns.httpx, "AsyncClient", _FakeHttpClient)
    return _FakeHttpClient


async def _set(session, key, value, bu=None):
    svc = ConfigService(session, get_credential_encryption_key())
    if bu:
        await svc.set_for_bu(bu, key, value)
    else:
        await svc.set(key, value)
    await session.commit()


# ─── pure helpers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "wd,region,expected",
    [
        ("account-123/us-east-1/relprod/ai", "us-east-1", "relprod/ai"),
        (".", None, ""),
        ("", None, ""),
        ("envs/dev/vpc", None, "envs/dev/vpc"),
    ],
)
def test_leaf_path(wd, region, expected):
    assert ns._leaf_path(wd, region) == expected


def test_plan_summary_str():
    assert ns._plan_summary_str(None, None, None) == ""
    assert ns._plan_summary_str(1, 2, 3) == "+1 ~2 −3"
    assert ns._plan_summary_str(None, 0, None) == "+0 ~0 −0"


def test_fields_block_skips_empty():
    b = ns._fields_block([("A", "x"), ("B", ""), ("C", "y")])
    assert [f["text"] for f in b["fields"]] == ["*A*\nx", "*C*\ny"]


def test_link_helpers(monkeypatch):
    monkeypatch.setenv("PUBLIC_UI_URL", "https://ui.example.com/")
    assert ns._public_base_url() == "https://ui.example.com"
    assert ns._run_link("r1") == "https://ui.example.com/runs/r1"
    assert ns._workspace_link("w1") == "https://ui.example.com/workspaces/w1"
    assert ns._link_button_block("Go", "u")["elements"][0]["url"] == "u"


def test_public_base_url_fallbacks(monkeypatch):
    monkeypatch.delenv("PUBLIC_UI_URL", raising=False)
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.com")
    assert ns._public_base_url() == "https://api.example.com"
    monkeypatch.delenv("PUBLIC_API_URL", raising=False)
    assert ns._public_base_url() == "http://localhost:8000"


# ─── webhook / slack-webhook / email ─────────────────────────────────────────


async def test_plan_approval_with_slack_and_email(db_session, fake_http, monkeypatch):
    monkeypatch.setenv("PUBLIC_UI_URL", "https://ui")
    await _set(db_session, "slack.webhook_url", "https://hooks.slack/ok")
    await _set(db_session, "smtp.host", "smtp.local")
    await _set(db_session, "smtp.to", "ops@x.com")
    await ns.send_plan_approval_notification(db_session, "run1", "ws", "PLAN", )
    assert any("hooks.slack" in u for u, _ in fake_http.last_posts)


async def test_plan_approval_slack_post_failure_swallowed(db_session, fake_http):
    await _set(db_session, "slack.webhook_url", "https://hooks.slack/boom")
    # no smtp configured → email no-ops; slack raises but is swallowed
    await ns.send_plan_approval_notification(db_session, "r", "ws", "p", api_base_url="http://x")


async def test_generic_webhook(db_session, fake_http):
    # unset → no-op
    await ns.send_generic_webhook(db_session, "evt", {"a": 1})
    assert fake_http.last_posts == []
    await _set(db_session, "notification.webhook_url", "https://hook/ok")
    await ns.send_generic_webhook(db_session, "evt", {"a": 1})
    assert fake_http.last_posts[-1][1] == {"event": "evt", "a": 1}
    # RequestError swallowed
    await _set(db_session, "notification.webhook_url", "https://hook/boom")
    await ns.send_generic_webhook(db_session, "evt2", {})


async def test_drift_alert_slack_and_email(db_session, fake_http):
    await _set(db_session, "slack.webhook_url", "https://hooks/ok")
    await ns.send_drift_alert(db_session, "ws", "stuff changed")
    assert any("hooks/ok" in u for u, _ in fake_http.last_posts)
    # boom path swallowed
    await _set(db_session, "slack.webhook_url", "https://hooks/boom")
    await ns.send_drift_alert(db_session, "ws", "x")


# ─── account colour on the legacy webhook path ───────────────────────────────


async def _ws_with_account(db_session, color="purple"):
    """A workspace whose AWS account is registered, so a badge resolves."""
    from app.models.aws_account import AwsAccount
    from app.services import aws_account_service as accs

    ws = await _make_ws(db_session, name="coloured-ws")
    db_session.add(
        AwsAccount(
            business_unit_id=DEFAULT_BU_ID,
            account_id=ws.aws_account_id,
            name="Prod-Account",
            state_bucket="b",
            state_bucket_region="us-east-1",
            default_region="us-east-1",
            color=color,
            access_key_id_encrypted=accs.encrypt_secret("AKIAX"),
            secret_access_key_encrypted=accs.encrypt_secret("s"),
        )
    )
    await db_session.commit()
    return ws


async def test_plan_approval_carries_account_stripe(db_session, fake_http):
    ws = await _ws_with_account(db_session)
    await _set(db_session, "slack.webhook_url", "https://hooks/ok")
    await ns.send_plan_approval_notification(
        db_session, "run1", "ws", "PLAN", workspace_id=ws.id
    )
    _, body = fake_http.last_posts[-1]
    # Blocks move inside one attachment — that's what draws Slack's left bar.
    assert body["attachments"][0]["color"] == "#7c3aed"
    assert "blocks" not in body
    # Top-level text (with the emoji) so mobile pushes aren't blank.
    assert body["text"].startswith("\U0001f7e3 ")
    # Account sits directly under the header, above the plan dump.
    blocks = body["attachments"][0]["blocks"]
    assert blocks[0]["type"] == "header"
    assert "Prod-Account" in blocks[1]["fields"][0]["text"]


async def test_drift_alert_carries_account_stripe(db_session, fake_http):
    ws = await _ws_with_account(db_session, color="green")
    await _set(db_session, "slack.webhook_url", "https://hooks/ok")
    await ns.send_drift_alert(db_session, "ws", "changed", workspace_id=ws.id)
    _, body = fake_http.last_posts[-1]
    assert body["attachments"][0]["color"] == "#059669"
    assert body["text"].startswith("\U0001f7e2 ")


async def test_legacy_senders_without_workspace_id_are_unstriped(db_session, fake_http):
    """Back-compat: a caller that passes no workspace_id still posts, plainly."""
    await _set(db_session, "slack.webhook_url", "https://hooks/ok")
    await ns.send_drift_alert(db_session, "ws", "changed")
    _, body = fake_http.last_posts[-1]
    assert "attachments" not in body
    assert body["blocks"]
    assert not body["text"].startswith(" ")


async def test_unregistered_account_gets_no_stripe(db_session, fake_http):
    """A workspace whose account isn't in aws_accounts must not invent a colour."""
    ws = await _make_ws(db_session, name="orphan-ws")
    await _set(db_session, "slack.webhook_url", "https://hooks/ok")
    await ns.send_drift_alert(db_session, "ws", "changed", workspace_id=ws.id)
    _, body = fake_http.last_posts[-1]
    assert "attachments" not in body


async def test_email_names_the_account(db_session, monkeypatch):
    """Slack and email must not disagree about which account is affected."""
    ws = await _ws_with_account(db_session)
    bodies = []
    monkeypatch.setattr(
        ns,
        "send_email_notification",
        lambda session, *, subject, body: bodies.append(body) or _noop(),
    )
    await ns.send_drift_alert(db_session, "ws", "changed", workspace_id=ws.id)
    # Plaintext, so the Slack mrkdwn backticks are stripped.
    assert "Account: Prod-Account (123456789012)" in bodies[0]
    assert "`" not in bodies[0]


async def _noop():
    return None


async def test_email_paths(db_session, monkeypatch):
    sent = {}

    class _SMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, msg):
            sent["subject"] = msg["Subject"]

    monkeypatch.setattr(ns.smtplib, "SMTP", _SMTP)

    # no host → no-op
    await ns.send_email_notification(db_session, "s", "b")
    assert sent == {}
    # host but no recipient → no-op
    await _set(db_session, "smtp.host", "smtp.local")
    await ns.send_email_notification(db_session, "s", "b")
    assert "subject" not in sent
    # full send with auth + starttls (port 587)
    await _set(db_session, "smtp.to", "ops@x.com")
    await _set(db_session, "smtp.username", "u")
    await _set(db_session, "smtp.password", "p")
    await ns.send_email_notification(db_session, "Subj", "body")
    assert sent["subject"] == "Subj" and sent["tls"] is True and sent["login"] == ("u", "p")


async def test_email_port_25_no_starttls(db_session, monkeypatch):
    flags = {}

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            flags["tls"] = True

        def send_message(self, msg):
            flags["sent"] = True

    monkeypatch.setattr(ns.smtplib, "SMTP", _SMTP)
    await _set(db_session, "smtp.host", "smtp.local")
    await _set(db_session, "smtp.port", "25")
    await _set(db_session, "smtp.to", "ops@x.com")
    await ns.send_email_notification(db_session, "s", "b")
    assert flags.get("tls") is None and flags["sent"] is True


async def test_email_send_failure_swallowed(db_session, monkeypatch):
    class _SMTP:
        def __init__(self, *a, **k):
            raise RuntimeError("connect refused")

    monkeypatch.setattr(ns.smtplib, "SMTP", _SMTP)
    await _set(db_session, "smtp.host", "smtp.local")
    await _set(db_session, "smtp.to", "ops@x.com")
    await ns.send_email_notification(db_session, "s", "b")  # must not raise


# ─── bot-token slack path + bu resolution ────────────────────────────────────


async def _seed_default_bu(db_session):
    # db_session uses its own engine (separate from the default_bu fixture's),
    # so seed the BU row here for code paths that look it up.
    from app.models.business_unit import BusinessUnit

    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
        await db_session.commit()


async def _make_ws(db_session, name="notif-ws"):
    await _seed_default_bu(db_session)
    ws = Workspace(
        business_unit_id=DEFAULT_BU_ID,
        name=name,
        aws_account_id="123456789012",
        region="us-east-1",
        environment="dev",
        tf_working_dir="account-1/us-east-1/team/leaf",
        repo_ref="main",
    )
    db_session.add(ws)
    await db_session.commit()
    return ws


async def test_resolve_bu_slug(db_session):
    assert await ns._resolve_bu_slug_for_workspace(db_session, "missing") is None
    ws = await _make_ws(db_session)
    assert await ns._resolve_bu_slug_for_workspace(db_session, ws.id) == "default"
    # workspace exists but its BU was deleted → None
    from app.models.business_unit import BusinessUnit

    bu = await db_session.get(BusinessUnit, DEFAULT_BU_ID)
    await db_session.delete(bu)
    await db_session.commit()
    assert await ns._resolve_bu_slug_for_workspace(db_session, ws.id) is None


async def test_send_slack_bot_notification_paths(db_session, monkeypatch):
    from app.routers.integrations import SLACK_BOT_TOKEN_KEY, SLACK_CHANNEL_ID_KEY

    # no creds → silent no-op
    await ns.send_slack_bot_notification(db_session, bu_slug="default", text="hi")

    await _set(db_session, SLACK_BOT_TOKEN_KEY, "xoxb", bu="default")
    await _set(db_session, SLACK_CHANNEL_ID_KEY, "C1", bu="default")

    calls = []

    async def ok(token, channel, text, blocks=None, color=None):
        calls.append((token, channel))

    monkeypatch.setattr(slack_svc, "post_message", ok)
    await ns.send_slack_bot_notification(db_session, bu_slug="default", text="hi")
    assert calls == [("xoxb", "C1")]

    # SlackError swallowed
    async def boom_code(*a, **k):
        raise slack_svc.SlackError("rate_limited")

    monkeypatch.setattr(slack_svc, "post_message", boom_code)
    await ns.send_slack_bot_notification(db_session, bu_slug="default", text="hi")

    # RequestError swallowed
    async def boom_net(*a, **k):
        import httpx

        raise httpx.RequestError("net", request=None)

    monkeypatch.setattr(slack_svc, "post_message", boom_net)
    await ns.send_slack_bot_notification(db_session, bu_slug="default", text="hi")


async def test_run_event_builders_resolve_and_skip(db_session, monkeypatch):
    ws = await _make_ws(db_session)
    posted = []

    async def rec(session, *, bu_slug, text, blocks=None, color=None, channel=None):
        posted.append(text)

    monkeypatch.setattr(ns, "send_slack_bot_notification", rec)

    await ns.send_slack_run_auto_approved(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r",
        skip_apply=True, region="us-east-1", working_dir="account-1/us-east-1/team/leaf",
        branch="main", command="apply", triggered_by_email="me@x.com",
    )
    await ns.send_slack_run_auto_approved(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r", skip_apply=False,
    )
    await ns.send_slack_run_awaiting_approval(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r",
        add=1, change=0, destroy=2, branch="main", command="plan", region="us-east-1",
        working_dir="account-1/us-east-1/team/leaf",
    )
    await ns.send_slack_run_awaiting_approval(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r",  # no plan numbers
    )
    await ns.send_slack_run_failed(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r", command="apply",
        failed_stage="apply", error_excerpt="boom error", region="us-east-1",
        working_dir="account-1/us-east-1/team/leaf",
    )
    await ns.send_slack_run_failed(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r", command="apply",
    )
    await ns.send_slack_drift_detected(
        db_session, workspace_id=ws.id, workspace_name="ws", summary="drifted",
        region="us-east-1", working_dir="account-1/us-east-1/team/leaf",
    )
    await ns.send_slack_drift_detected(
        db_session, workspace_id=ws.id, workspace_name="ws", summary="",
    )
    assert len(posted) == 8

    # bu_slug None (missing workspace) → all builders early-return (no post)
    posted.clear()
    await ns.send_slack_run_auto_approved(
        db_session, workspace_id="missing", workspace_name="ws", run_id="r", skip_apply=False
    )
    await ns.send_slack_run_awaiting_approval(
        db_session, workspace_id="missing", workspace_name="ws", run_id="r"
    )
    await ns.send_slack_run_failed(
        db_session, workspace_id="missing", workspace_name="ws", run_id="r", command="apply"
    )
    await ns.send_slack_drift_detected(
        db_session, workspace_id="missing", workspace_name="ws", summary="s"
    )
    assert posted == []


async def test_drift_alert_routing(db_session, monkeypatch):
    """Settings → Slack → Drift alerts: default channel unless overridden,
    nothing at all when switched off. Run notifications ignore the override."""
    from app.routers.integrations import (
        SLACK_BOT_TOKEN_KEY,
        SLACK_CHANNEL_ID_KEY,
        SLACK_DRIFT_CHANNEL_ID_KEY,
        SLACK_DRIFT_ENABLED_KEY,
    )

    ws = await _make_ws(db_session)
    await _set(db_session, SLACK_BOT_TOKEN_KEY, "xoxb", bu="default")
    await _set(db_session, SLACK_CHANNEL_ID_KEY, "C-DEFAULT", bu="default")
    calls = []

    async def ok(token, channel, text, blocks=None, color=None):
        calls.append(channel)

    monkeypatch.setattr(slack_svc, "post_message", ok)

    async def drift():
        await ns.send_slack_drift_detected(
            db_session, workspace_id=ws.id, workspace_name="ws", summary="tags changed: +x"
        )

    await drift()
    assert calls == ["C-DEFAULT"]

    await _set(db_session, SLACK_DRIFT_CHANNEL_ID_KEY, "C-DRIFT", bu="default")
    await drift()
    await ns.send_slack_run_failed(
        db_session, workspace_id=ws.id, workspace_name="ws", run_id="r", command="apply"
    )
    assert calls == ["C-DEFAULT", "C-DRIFT", "C-DEFAULT"]

    await _set(db_session, SLACK_DRIFT_ENABLED_KEY, "false", bu="default")
    await drift()
    assert calls == ["C-DEFAULT", "C-DRIFT", "C-DEFAULT"]


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
    # db_session uses its own engine, separate from the default_bu fixture's
    # (see _seed_default_bu above) — seed the BU row here too, or
    # _resolve_bu_slug_for_workspace can't find it and the send no-ops.
    await _seed_default_bu(db_session)
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
    await _seed_default_bu(db_session)
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
    await _seed_default_bu(db_session)
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
    await _seed_default_bu(db_session)
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
