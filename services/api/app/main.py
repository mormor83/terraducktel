import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

# Configure JSON logging BEFORE anything else logs (uvicorn imports below
# would otherwise install their own handlers and double-emit).
from app.observability import configure_logging  # noqa: E402

configure_logging()

from app.routers import (  # noqa: E402
    api_keys,
    audit,
    auth,
    aws_accounts,
    azure_subscriptions,
    approvals,
    business_units,
    clusters,
    drift,
    environments,
    gcp_projects,
    integrations,
    internal,
    inventory,
    policies,
    presence,
    runs,
    runtime_config,
    state,
    users,
    variables,
    webhooks,
    workspace_variables,
    workspaces,
)

logger = logging.getLogger(__name__)

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate critical config at startup; spin up background tasks.

    Logs a hard error if `TERRADUCKTEL_STATE_TOKEN` or `TERRADUCKTEL_INTERNAL_TOKEN`
    is missing — state/internal endpoints return 503 until the operator sets
    the relevant env var, so this gives them a clear signal at boot rather
    than waiting for a Terraform run or a drift-detector cycle. The two are
    deliberately separate secrets — do not merge these checks.

    Also kicks off the executor-job worker and the stale-job reaper. Both are
    in-process asyncio tasks; they're cancelled cleanly on shutdown.
    """
    import asyncio

    from app.auth.internal_token import _expected_internal_token, _expected_token
    from app.db import AsyncSessionLocal
    from app.services.repo_sync import repo_sync_loop
    from app.services.run_worker import gauges_loop, reaper_loop, worker_loop

    try:
        _expected_token()
    except RuntimeError:
        logger.error(
            "TERRADUCKTEL_STATE_TOKEN is not configured; state endpoints will return 503"
        )
    try:
        _expected_internal_token()
    except RuntimeError:
        logger.error(
            "TERRADUCKTEL_INTERNAL_TOKEN is not configured; internal endpoints will return 503"
        )

    worker_task = asyncio.create_task(worker_loop(AsyncSessionLocal), name="run-worker")
    reaper_task = asyncio.create_task(reaper_loop(AsyncSessionLocal), name="run-reaper")
    gauges_task = asyncio.create_task(gauges_loop(AsyncSessionLocal), name="metrics-gauges")
    # Periodic path-sync — labels each workspace as ok / orphaned so the
    # dashboard can flag rows whose source folder was deleted/renamed in
    # the repo. Same in-process pattern as the run worker.
    sync_task = asyncio.create_task(repo_sync_loop(AsyncSessionLocal), name="repo-sync")
    tasks = (worker_task, reaper_task, gauges_task, sync_task)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="Terraducktel API",
    version=VERSION,
    lifespan=lifespan,
    # The public ALB only forwards `/api/*` to this container, so park the
    # OpenAPI spec and Swagger/Redoc UIs under that prefix — anything outside
    # `/api/*` is unreachable from the browser.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)


# SessionMiddleware: required by authlib's OIDC handshake (stashes state/nonce
# in a signed cookie across the IdP redirect). Harmless when OIDC is disabled —
# the cookie is only ever written during /api/v1/auth/oidc/login. The session
# secret is bootstrapped from CREDENTIAL_ENCRYPTION_KEY so we don't introduce a
# new mandatory env var; rotate by rotating that key (re-encrypt script handles
# data keys; session cookies just invalidate, which is fine).
#
# Fail-loud, same as get_credential_encryption_key()/JWT_SECRET_KEY: a
# misconfigured CREDENTIAL_ENCRYPTION_KEY must stop the API from starting, not
# silently downgrade to a hardcoded, source-visible session secret ( —
# that fallback string would let anyone who has read this file forge a
# validly-signed OIDC login-CSRF session cookie).
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.auth.encryption_key import get_credential_encryption_key  # noqa: E402


def _derive_session_secret() -> str:
    """Domain-separate the session-cookie signing key from the credential
    encryption key.

    The raw CREDENTIAL_ENCRYPTION_KEY encrypts AWS/Azure/kubeconfig/config
    secrets at rest. Using it verbatim as the session-cookie HMAC secret
    collapses those trust domains onto one value. Run it through HKDF with a
    distinct `info` label — exactly as the Fernet paths do — so the session
    secret is cryptographically independent while still bootstrapping from the
    single configured key (no new mandatory env var).
    """
    import base64

    from cryptography.hazmat.primitives import hashes  # noqa: E402
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"terraducktel-session-v1",
        info=b"session-cookie-signing",
    ).derive(get_credential_encryption_key())
    return base64.urlsafe_b64encode(derived).decode()


_session_secret = _derive_session_secret()

# Secure by default (cookie only sent over HTTPS). nginx/traefik terminate
# TLS in front of this container, so the cookie's actual trip from browser
# to proxy is HTTPS in any real deployment — hardcoding False here meant a
# proxy misconfiguration would silently downgrade to sending it in the
# clear. Opt out only for local, TLS-less setups that also exercise OIDC
# (plain local/password auth never touches this cookie at all).
_session_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE", "true").strip().lower() not in (
    "false", "0", "no",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    https_only=_session_cookie_secure,
    same_site="lax",
    max_age=600,  # 10 minutes — only needs to survive the IdP round-trip
)

# Simple in-process metrics counters (Prometheus text format)
_REQUEST_COUNT: dict[str, int] = {}
_REQUEST_LATENCY_SUM: dict[str, float] = {}


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.monotonic()
    response: Response = await call_next(request)
    elapsed = time.monotonic() - start
    # Use the matched route template (e.g. /api/v1/runs/{run_id}) instead of the
    # concrete path so workspace/run UUIDs don't leak into Prometheus labels.
    route = request.scope.get("route")
    path_label = getattr(route, "path", None) or request.url.path
    key = f"{request.method} {path_label} {response.status_code}"
    _REQUEST_COUNT[key] = _REQUEST_COUNT.get(key, 0) + 1
    _REQUEST_LATENCY_SUM[key] = _REQUEST_LATENCY_SUM.get(key, 0.0) + elapsed
    return response


# Register routers
app.include_router(auth.router)
app.include_router(aws_accounts.router)
app.include_router(azure_subscriptions.router)
app.include_router(gcp_projects.router)
app.include_router(integrations.router)
app.include_router(internal.router)
app.include_router(workspaces.router)
app.include_router(runs.router)
app.include_router(approvals.router)
app.include_router(webhooks.router)
app.include_router(users.router)
app.include_router(state.router)
app.include_router(audit.router)
app.include_router(drift.router)
app.include_router(inventory.router)
app.include_router(environments.router)
app.include_router(runtime_config.router)
app.include_router(variables.router)
app.include_router(workspace_variables.router)
app.include_router(business_units.router)
app.include_router(clusters.router)
app.include_router(presence.router)
app.include_router(api_keys.router)
app.include_router(policies.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": VERSION}


# Land users who hit /api or /api/ on the Swagger UI rather than a 404.
@app.get("/api", include_in_schema=False)
@app.get("/api/", include_in_schema=False)
async def api_root() -> RedirectResponse:
    return RedirectResponse(url="/api/docs")


@app.get("/api/v1/server-info")
async def server_info() -> dict:
    """Public, unauthenticated server config for the UI to feature-toggle.

    4-eyes was removed; the keys are kept (empty / false) so legacy clients
    don't crash on a missing field.
    """
    return {
        "version": VERSION,
        "four_eyes_branches": [],
        "four_eyes": False,
    }


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus-compatible metrics endpoint.

    Two sources: the in-process HTTP request counters above + the business
    metrics in `app.observability` (run duration, queue depth, drift age, …).
    They share no state so we serialise both into the same response.
    """
    from app.observability import render_prom_text

    lines = [
        "# HELP http_requests_total Total HTTP requests",
        "# TYPE http_requests_total counter",
    ]
    for key, count in sorted(_REQUEST_COUNT.items()):
        method, path, status = key.split(" ", 2)
        lines.append(f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')
    lines += [
        "# HELP http_request_duration_seconds_sum Total latency in seconds",
        "# TYPE http_request_duration_seconds_sum counter",
    ]
    for key, total in sorted(_REQUEST_LATENCY_SUM.items()):
        method, path, status = key.split(" ", 2)
        lines.append(
            f'http_request_duration_seconds_sum{{method="{method}",path="{path}",status="{status}"}} {total:.6f}'
        )

    biz = render_prom_text()
    body = "\n".join(lines) + "\n" + biz
    return Response(content=body, media_type="text/plain; charset=utf-8")
