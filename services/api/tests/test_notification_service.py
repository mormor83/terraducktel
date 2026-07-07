"""Unit coverage for notification_service: Slack webhook + bot, generic webhook,
SMTP email, drift alerts, run-event blocks, and the link/leaf helpers.
httpx.AsyncClient and smtplib.SMTP are mocked — no real network."""
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

    async def ok(token, channel, text, blocks=None):
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

    async def rec(session, *, bu_slug, text, blocks=None):
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
