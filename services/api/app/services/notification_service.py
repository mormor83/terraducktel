"""Notification delivery: Slack, generic webhooks, and SMTP email."""
import email.message
import logging
import os
import smtplib

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


def _config_svc(session: AsyncSession) -> ConfigService:
    return ConfigService(session, get_credential_encryption_key())


async def get_slack_webhook_url(session: AsyncSession) -> str | None:
    return await _config_svc(session).get("slack.webhook_url")


async def send_plan_approval_notification(
    session: AsyncSession,
    run_id: str,
    workspace_name: str,
    plan_summary: str,
    *,
    api_base_url: str | None = None,
) -> None:
    """POST Slack Block Kit message with plan excerpt and approve/reject links."""
    url = await get_slack_webhook_url(session)
    # Prefer PUBLIC_UI_URL for the user-facing links; PUBLIC_API_URL is the
    # back-compat fallback for deployments where UI + API share a host.
    base = (
        api_base_url
        or os.environ.get("PUBLIC_UI_URL")
        or os.environ.get("PUBLIC_API_URL")
        or "http://localhost:8000"
    ).rstrip("/")
    excerpt = (plan_summary or "")[:2000]

    if url:
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"Terraform plan ready: {workspace_name}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```{excerpt}\n```"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve"},
                            "style": "primary",
                            "url": f"{base}/runs/{run_id}/approve",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject"},
                            "style": "danger",
                            "url": f"{base}/runs/{run_id}/reject",
                        },
                    ],
                },
            ]
        }
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=30.0)
        except Exception:
            logger.warning(
                "Failed to send Slack plan approval notification (continuing)",
                exc_info=True,
            )

    await send_email_notification(
        session,
        subject=f"Terraform plan ready: {workspace_name}",
        body=(
            f"Run {run_id} is awaiting approval for workspace '{workspace_name}'.\n\n"
            f"Plan excerpt:\n{excerpt}\n\n"
            f"Approve: {base}/runs/{run_id}/approve\n"
            f"Reject:  {base}/runs/{run_id}/reject"
        ),
    )


async def send_generic_webhook(
    session: AsyncSession,
    event: str,
    payload: dict,
) -> None:
    """POST JSON to notification.webhook_url when configured.

    Best-effort: transient outages at the webhook target are logged, not surfaced.
    """
    svc = _config_svc(session)
    url = await svc.get("notification.webhook_url")
    if not url:
        return
    body = {"event": event, **payload}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=body, timeout=30.0)
    except httpx.RequestError:
        logger.warning("Generic webhook failed for event %s", event, exc_info=True)


async def send_drift_alert(
    session: AsyncSession,
    workspace_name: str,
    summary: str,
) -> None:
    """Slack + email alert when drift is detected.

    Best-effort: a transient Slack outage during drift detection must not
    propagate to the drift-detector caller (which would otherwise mark the
    drift run as failed and skip subsequent workspaces).
    """
    url = await get_slack_webhook_url(session)
    if url:
        text = f"*Drift detected* — `{workspace_name}`\n{summary}"
        payload = {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=30.0)
        except httpx.RequestError:
            logger.warning("Slack drift alert failed for workspace %s", workspace_name, exc_info=True)

    await send_email_notification(
        session,
        subject=f"Drift detected: {workspace_name}",
        body=f"Drift was detected in workspace '{workspace_name}'.\n\nSummary:\n{summary}",
    )


# ─── Bot-token-based Slack notifications (per-BU) ──────────────────────────
#
# These replace / supplement the legacy `slack.webhook_url` path with the
# Settings → Slack flow: bot token + chosen public channel, stored per-BU.
# Each helper is best-effort — a Slack outage logs a warning and continues.


async def _slack_bot_creds(
    session: AsyncSession, bu_slug: str
) -> tuple[str | None, str | None]:
    from app.routers.integrations import (
        SLACK_BOT_TOKEN_KEY,
        SLACK_CHANNEL_ID_KEY,
    )

    svc = _config_svc(session)
    token = await svc.get_for_bu(bu_slug, SLACK_BOT_TOKEN_KEY)
    channel = await svc.get_for_bu(bu_slug, SLACK_CHANNEL_ID_KEY)
    return (token, channel)


async def _resolve_bu_slug_for_workspace(
    session: AsyncSession, workspace_id: str
) -> str | None:
    """Look up a workspace's BU slug. Returns None if either the workspace
    or its BU has been deleted — callers treat that as 'no notification'."""
    from sqlalchemy import select

    from app.models.business_unit import BusinessUnit
    from app.models.workspace import Workspace

    ws = await session.get(Workspace, workspace_id)
    if ws is None or not ws.business_unit_id:
        return None
    bu = await session.get(BusinessUnit, ws.business_unit_id)
    return bu.slug if bu else None


async def send_slack_bot_notification(
    session: AsyncSession,
    *,
    bu_slug: str,
    text: str,
    blocks: list | None = None,
) -> None:
    """Post to the BU's configured Slack channel via bot token.

    Silently no-ops when the BU has no Slack config. All errors are
    swallowed and logged — notification must never break the calling
    flow (run PATCH, drift detector, etc.).
    """
    from app.services import slack as slack_svc

    token, channel = await _slack_bot_creds(session, bu_slug)
    if not token or not channel:
        return
    try:
        await slack_svc.post_message(token, channel, text, blocks=blocks)
    except slack_svc.SlackError as e:
        logger.warning(
            "Slack post failed for BU %s (channel=%s): %s",
            bu_slug, channel, e.code,
        )
    except httpx.RequestError:
        logger.warning(
            "Slack network error for BU %s (channel=%s)",
            bu_slug, channel, exc_info=True,
        )


def _public_base_url() -> str:
    """Base URL to use in Slack/email links. Prefers PUBLIC_UI_URL (where
    the UI lives) over PUBLIC_API_URL (where the API lives) for the cases
    where they're served from different hosts; falls back to the API URL
    and finally localhost for dev."""
    return (
        os.environ.get("PUBLIC_UI_URL")
        or os.environ.get("PUBLIC_API_URL")
        or "http://localhost:8000"
    ).rstrip("/")


def _run_link(run_id: str) -> str:
    # NOTE: actual route is /runs/{id}; older code prepended /ui/, which
    # 404s in production. Keep this aligned with services/ui/src/App.tsx.
    return f"{_public_base_url()}/runs/{run_id}"


def _workspace_link(workspace_id: str) -> str:
    return f"{_public_base_url()}/workspaces/{workspace_id}"


def _leaf_path(tf_working_dir: str | None, region: str | None = None) -> str:
    """Repo-relative path to the workspace leaf, with the leading
    `account-<id>/` and `<region>/` segments stripped — mirrors the UI's
    `workspacePathSegments` so Slack shows `relprod/ai-cog` instead of
    `account-222222222222/us-east-1/relprod/ai-cog`. Empty for `.`/unset."""
    raw = (tf_working_dir or "").strip().strip("/")
    if not raw or raw == ".":
        return ""
    parts = [p for p in raw.split("/") if p]
    if parts and parts[0].startswith("account-"):
        parts = parts[1:]
    if parts and region and parts[0] == region:
        parts = parts[1:]
    return "/".join(parts)


def _fields_block(pairs: list[tuple[str, str]]) -> dict:
    """Build a Slack `section.fields` block from (label, value) pairs.

    Slack caps fields at 10 entries and ~2000 chars per field — well above
    anything we put here. Empty / falsy values are skipped so we don't
    render rows like "Branch:" with nothing after.
    """
    fields = []
    for label, value in pairs:
        if not value:
            continue
        fields.append({"type": "mrkdwn", "text": f"*{label}*\n{value}"})
    return {"type": "section", "fields": fields}


def _link_button_block(label: str, url: str) -> dict:
    """Single-button actions row, used to open the run / workspace in the
    UI. Keeping the link inline as `<url|text>` ALSO in the message text
    means Slack's preview / mobile notifications stay informative even
    when the actions block doesn't render."""
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": label},
                "url": url,
            }
        ],
    }


def _plan_summary_str(add: int | None, change: int | None, destroy: int | None) -> str:
    if add is None and change is None and destroy is None:
        return ""
    return f"+{add or 0} ~{change or 0} −{destroy or 0}"


async def send_slack_run_auto_approved(
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
    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _run_link(run_id)
    leaf = _leaf_path(working_dir, region)
    text = f"✅ Auto-approved — {workspace_name} (no changes; <{link}|view run>)"
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"✅ *Auto-approved (0/0/0)* — `{workspace_name}`"
                    + ("\n_apply phase skipped_" if skip_apply else "")
                ),
            },
        },
        _fields_block(
            [
                ("Workspace", workspace_name or ""),
                ("Path", f"`{leaf}`" if leaf else ""),
                ("Region", region or ""),
                ("Branch", f"`{branch}`" if branch else ""),
                ("Command", command or ""),
                ("Triggered by", triggered_by_email or ""),
            ]
        ),
        _link_button_block("View run", link),
    ]
    await send_slack_bot_notification(session, bu_slug=bu_slug, text=text, blocks=blocks)


async def send_slack_run_awaiting_approval(
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
    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _run_link(run_id)
    leaf = _leaf_path(working_dir, region)
    plan = _plan_summary_str(add, change, destroy)
    text = (
        f"⏸ Awaiting approval — {workspace_name}"
        + (f" · {plan}" if plan else "")
        + f" (<{link}|review>)"
    )
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⏸ *Awaiting approval* — `{workspace_name}`",
            },
        },
        _fields_block(
            [
                ("Workspace", workspace_name or ""),
                ("Path", f"`{leaf}`" if leaf else ""),
                ("Region", region or ""),
                ("Branch", f"`{branch}`" if branch else ""),
                ("Command", command or ""),
                ("Plan", plan),
                ("Triggered by", triggered_by_email or ""),
            ]
        ),
        _link_button_block("Review run", link),
    ]
    await send_slack_bot_notification(session, bu_slug=bu_slug, text=text, blocks=blocks)


async def send_slack_run_failed(
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
    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    link = _run_link(run_id)
    leaf = _leaf_path(working_dir, region)
    text = (
        f"❌ Run failed — {workspace_name} ({command})"
        + (f" at {failed_stage}" if failed_stage else "")
        + f" (<{link}|view>)"
    )
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"❌ *Run failed* — `{workspace_name}` ({command})",
            },
        },
        _fields_block(
            [
                ("Workspace", workspace_name or ""),
                ("Path", f"`{leaf}`" if leaf else ""),
                ("Region", region or ""),
                ("Branch", f"`{branch}`" if branch else ""),
                ("Failed stage", failed_stage or ""),
                ("Triggered by", triggered_by_email or ""),
            ]
        ),
    ]
    excerpt = (error_excerpt or "")[:600].strip()
    if excerpt:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{excerpt}```"}}
        )
    blocks.append(_link_button_block("View run", link))
    await send_slack_bot_notification(session, bu_slug=bu_slug, text=text, blocks=blocks)


async def send_slack_drift_detected(
    session: AsyncSession,
    *,
    workspace_id: str,
    workspace_name: str,
    summary: str,
    environment: str | None = None,
    region: str | None = None,
    working_dir: str | None = None,
) -> None:
    bu_slug = await _resolve_bu_slug_for_workspace(session, workspace_id)
    if not bu_slug:
        return
    excerpt = (summary or "")[:800].strip()
    leaf = _leaf_path(working_dir, region)
    link = _workspace_link(workspace_id)
    text = f"⚠ Drift detected — {workspace_name} (<{link}|view>)"
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠ *Drift detected* — `{workspace_name}`",
            },
        },
        _fields_block(
            [
                ("Workspace", workspace_name or ""),
                ("Path", f"`{leaf}`" if leaf else ""),
                ("Region", region or ""),
            ]
        ),
    ]
    if excerpt:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{excerpt}```"}}
        )
    blocks.append(_link_button_block("View workspace", link))
    await send_slack_bot_notification(session, bu_slug=bu_slug, text=text, blocks=blocks)


async def send_email_notification(
    session: AsyncSession,
    subject: str,
    body: str,
) -> None:
    """Send email via SMTP when configured (smtp.host, smtp.port, smtp.from, smtp.to).

    Optional: smtp.username / smtp.password for authenticated relay.
    Silently skips if SMTP is not configured.
    """
    svc = _config_svc(session)
    smtp_host = await svc.get("smtp.host")
    if not smtp_host:
        return

    smtp_port = int(await svc.get("smtp.port") or "587")
    smtp_from = await svc.get("smtp.from") or "noreply@terraducktel.local"
    smtp_to = await svc.get("smtp.to")
    if not smtp_to:
        return

    smtp_user = await svc.get("smtp.username")
    smtp_pass = await svc.get("smtp.password")

    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = smtp_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info("Email notification sent to %s: %s", smtp_to, subject)
    except Exception:
        logger.warning("Failed to send email notification", exc_info=True)
