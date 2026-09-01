"""Browser SSO for the CLI: the RFC 8252 loopback flow.

    tdt login --sso

1. Bind an ephemeral listener on 127.0.0.1 (the OS picks a free port).
2. Open the browser at `/auth/oidc/login?cli_port=…&cli_nonce=…`.
3. The IdP authenticates the human and returns to the API's own registered
   callback — the IdP never needs to know about the loopback port.
4. The API mints a TDT token pair and bounces the browser to our listener.
5. We check the nonce, keep the pair, and shut the listener down.

The listener binds loopback only, serves until a valid callback arrives (other
paths get a 404, a bad nonce a 400), and is torn down on success, failure or
timeout so a failed login can't leave a socket bound.
"""
from __future__ import annotations

import http.server
import secrets
import socket
import threading
import urllib.parse
import webbrowser

from . import credentials as creds
from .config import Profile
from .errors import ExitCode, TdtError
from .output import echo

_TIMEOUT_SECONDS = 300


class _Result:
    def __init__(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.nonce: str | None = None
        self.error: str | None = None
        self.done = threading.Event()


def _make_handler(result: _Result, expected_nonce: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        # Silence the default stderr access log — it would interleave with our
        # own terminal output for no benefit.
        def log_message(self, *args):  # noqa: D102
            pass

        def _reply(self, status: int, message: str) -> None:
            body = (
                "<!doctype html><meta name='referrer' content='no-referrer'>"
                "<title>Terraducktel CLI</title>"
                "<body style=\"font:14px system-ui;padding:3rem;text-align:center\">"
                f"<p>{message}</p></body>"
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — stdlib naming
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self._reply(404, "Not found.")
                return
            params = urllib.parse.parse_qs(parsed.query)
            nonce = (params.get("nonce") or [""])[0]

            # Constant-time compare: this is the only thing tying the callback to
            # the login this process started.
            if not secrets.compare_digest(nonce, expected_nonce):
                result.error = "callback nonce did not match the login request"
                self._reply(400, "Sign-in could not be verified. Check your terminal.")
                result.done.set()
                return

            access = (params.get("access_token") or [""])[0]
            refresh = (params.get("refresh_token") or [""])[0]
            if not access or not refresh:
                result.error = "callback did not carry a token pair"
                self._reply(400, "Sign-in failed. Check your terminal.")
                result.done.set()
                return

            result.access_token = access
            result.refresh_token = refresh
            self._reply(200, "Signed in. You can close this tab.")
            result.done.set()

    return Handler


def ensure_supported(config: dict) -> None:
    """Fail fast when the deployment predates the loopback hand-off.

    An older API ignores `cli_port` and redirects the browser to the SPA, so the
    human sees a successful UI login while the CLI waits out its full timeout for
    a callback that will never arrive. That is a five-minute silent hang, so
    check the capability marker before binding a listener at all.
    """
    if config.get("cli_loopback"):
        return
    raise TdtError(
        "This TDT deployment does not support CLI browser sign-in yet.",
        ExitCode.USAGE,
        hint=(
            "Its /auth/config reports no `cli_loopback` capability, so --sso would\n"
            "log you in to the web UI and leave this command hanging.\n"
            "Either deploy an API build that includes the loopback hand-off, or:\n"
            "  tdt login --api-key      # long-lived key from the UI (works today)"
        ),
    )


def login_sso(profile: Profile, *, open_browser: bool = True) -> creds.Credential:
    """Run the loopback flow and return the resulting credential."""
    nonce = secrets.token_urlsafe(32)
    result = _Result()

    # Port 0 → the OS assigns a free port, which we then read back. Binding
    # before we advertise the port means nothing else can claim it first.
    try:
        server = http.server.HTTPServer(("127.0.0.1", 0), _make_handler(result, nonce))
    except OSError as exc:
        raise TdtError(f"Could not bind a loopback listener: {exc}", ExitCode.API) from exc
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    query = urllib.parse.urlencode({"cli_port": port, "cli_nonce": nonce})
    auth_url = f"{profile.url}/auth/oidc/login?{query}"

    try:
        opened = webbrowser.open(auth_url) if open_browser else False
        if opened:
            echo("[dim]Opened your browser to sign in…[/dim]")
        else:
            # Headless box or SSH session — the human can still complete it, as
            # long as their browser can reach both the API and this loopback port.
            echo("Open this URL in a browser on THIS machine to sign in:\n")
            echo(f"  {auth_url}\n")

        if not result.done.wait(timeout=_TIMEOUT_SECONDS):
            raise TdtError(
                f"Timed out after {_TIMEOUT_SECONDS}s waiting for the browser.",
                ExitCode.TIMEOUT,
                hint="Re-run `tdt login --sso`, or use --api-key for an unattended login.",
            )
    finally:
        server.shutdown()
        server.server_close()

    if result.error:
        raise TdtError(
            f"SSO sign-in failed: {result.error}",
            ExitCode.AUTH,
            hint="Start a fresh `tdt login --sso` rather than reusing a stale browser tab.",
        )

    return creds.Credential(
        kind="jwt",
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )


def loopback_reachable() -> bool:
    """Whether we can bind loopback at all — a clearer error than a stack trace."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False
