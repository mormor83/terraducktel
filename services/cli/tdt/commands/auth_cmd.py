"""`tdt login` / `logout` / `whoami` / `profile …`."""
from __future__ import annotations

import typer

from .. import credentials as creds
from ..client import auth_config, login_password
from ..config import config_path, list_profiles, save_profile
from ..errors import ExitCode, TdtError
from ..output import Fmt, echo, ok, render, warn
from ..sso import ensure_supported, login_sso
from ..state import AppCtx

app = typer.Typer(no_args_is_help=True, help="Profiles: where the CLI points and who it is.")


def login(
    ctx: typer.Context,
    sso: bool = typer.Option(
        False, "--sso", help="Browser sign-in via the IdP (loopback flow). Default when OIDC is on."
    ),
    password: bool = typer.Option(
        False, "--password", help="Sign in with email + password (prompts for both)."
    ),
    email: str | None = typer.Option(None, "--email", help="Email, for --password."),
    api_key: bool = typer.Option(
        False, "--api-key", help="Paste a long-lived `tdt_…` API key (best for CI)."
    ),
    paste: bool = typer.Option(
        False, "--paste", help="Paste a refresh token you already hold. Rarely needed — prefer --sso.",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="With --sso, print the URL instead of opening a browser.",
    ),
) -> None:
    """Store credentials for the current profile.

    With no flags the CLI asks the server which providers are enabled
    (`GET /auth/config`) and picks: `--sso` when OIDC is on, `--password`
    otherwise. Either way the credential self-refreshes afterwards, so this is a
    once-per-machine step, not a daily one.

    `--api-key` is the unattended path for CI (no refresh, forces its own BU).
    """
    obj: AppCtx = ctx.obj
    prof = obj.profile
    chosen = [f for f in (sso, password, api_key, paste) if f]
    if len(chosen) > 1:
        raise TdtError(
            "Pick one of --sso / --password / --api-key / --paste.", ExitCode.USAGE
        )

    server_cfg: dict | None = None
    if not chosen:
        # Ask the deployment rather than guessing. A 'local'-only server has no
        # IdP to bounce off; an oidc-only one has no password to prompt for.
        server_cfg = auth_config(prof)
        if server_cfg.get("oidc_enabled") and server_cfg.get("cli_loopback"):
            sso = True
            echo("[dim]OIDC is enabled on this deployment — using browser sign-in.[/dim]")
        elif server_cfg.get("oidc_enabled"):
            # OIDC is on but this build can't hand tokens back to the CLI, so
            # picking --sso here would hang. Say so instead of guessing again.
            raise TdtError(
                "This deployment uses OIDC, but its API predates CLI browser sign-in.",
                ExitCode.USAGE,
                hint=(
                    "Use a long-lived key from the UI for now:\n"
                    "  tdt login --api-key\n"
                    "Or deploy an API build with the loopback hand-off, then `tdt login`."
                ),
            )
        else:
            password = True
            echo("[dim]No OIDC configured — using password sign-in.[/dim]")

    if sso:
        # Explicit --sso gets the same guard, so it can't hang either.
        ensure_supported(server_cfg if server_cfg is not None else auth_config(prof))
        cred = login_sso(prof, open_browser=not no_browser)
    elif api_key:
        key = typer.prompt("API key (tdt_…)", hide_input=True).strip()
        if not key.startswith("tdt_"):
            raise TdtError("That doesn't look like a TDT API key (expected `tdt_…`).", ExitCode.USAGE)
        cred = creds.Credential(kind="api_key", api_key=key)
    elif paste:
        token = typer.prompt("Refresh token", hide_input=True).strip()
        if token.startswith("tdt_"):
            raise TdtError(
                "That's an API key, not a refresh token — rerun with --api-key.",
                ExitCode.USAGE,
            )
        claims = creds.jwt_claims(token)
        if claims.get("type") != "refresh":
            raise TdtError(
                f"That token has type={claims.get('type') or 'unknown'!r}, not 'refresh'.",
                ExitCode.USAGE,
                hint="Copy the *refresh* token, not the access token.",
            )
        cred = creds.Credential(kind="jwt", refresh_token=token)
    else:
        addr = email or typer.prompt("Email")
        secret = typer.prompt("Password", hide_input=True)
        cred = login_password(prof, addr, secret)

    creds.store(prof.name, cred)

    # Prove the credential actually works before declaring success — a stored
    # token that 401s on first use is worse than a failed login.
    obj._client = None
    obj.client.cred = cred
    obj.client.get("/workspaces")
    ok(f"Logged in to profile '{prof.name}' ({prof.url}).")
    if cred.kind == "api_key":
        warn("API keys force their own BU — the --bu flag is ignored for this credential.")


def logout(ctx: typer.Context) -> None:
    """Forget the current profile's stored credential."""
    obj: AppCtx = ctx.obj
    name = obj.profile.name
    if creds.clear(name):
        ok(f"Cleared credentials for '{name}'.")
    else:
        echo(f"[dim]No stored credentials for '{name}'.[/dim]")


def whoami(ctx: typer.Context) -> None:
    """Show who the stored credential authenticates as, and prove it still works.

    The API has no `/me` endpoint, so identity is read from the token's own
    claims (unverified — display only) and liveness is proven by a real request.
    """
    obj: AppCtx = ctx.obj
    prof = obj.profile
    cred = creds.load(prof.name)
    if cred is None:
        raise TdtError(
            f"Not logged in for profile '{prof.name}'.", ExitCode.AUTH, hint="Run: tdt login"
        )

    obj.client.get("/workspaces")  # liveness + authorization probe

    info: dict = {"profile": prof.name, "url": prof.url, "bu": prof.bu or "(caller default)"}
    if cred.kind == "api_key":
        info["credential"] = "API key (tdt_…), BU forced server-side"
    else:
        claims = creds.jwt_claims(obj.client.cred.access_token or "")
        info["credential"] = "JWT (auto-refreshing)"
        info["email"] = claims.get("email")
        info["role"] = claims.get("role")
        info["superadmin"] = claims.get("is_superadmin")
        remaining = creds.seconds_until_expiry(obj.client.cred.access_token or "")
        if remaining is not None:
            info["access_token_expires_in"] = f"{int(remaining // 60)}m"
    render(obj.fmt, {k: v for k, v in info.items() if v is not None})


@app.command("list")
def profile_list(ctx: typer.Context) -> None:
    """List configured profiles."""
    obj: AppCtx = ctx.obj
    profiles = list_profiles()
    if not profiles:
        echo(f"[dim]No profiles in {config_path()}.[/dim]")
        echo("Add one: tdt profile add prod --url https://tdt.example.com --bu home")
        return
    from ..config import default_profile_name

    default = default_profile_name()
    rows = [
        {
            "name": name,
            "url": entry.get("url"),
            "bu": entry.get("bu"),
            "default": name == default,
            "logged_in": creds.load(name) is not None,
        }
        for name, entry in sorted(profiles.items())
    ]
    if obj.fmt is Fmt.json:
        # `default` stays a real boolean here — a " (default)" suffix glued onto
        # the name would break anyone matching on it with jq.
        render(obj.fmt, rows)
        return
    render(
        obj.fmt,
        [{**r, "name": r["name"] + (" (default)" if r["default"] else "")} for r in rows],
        [("NAME", "name"), ("URL", "url"), ("BU", "bu"), ("LOGGED IN", "logged_in")],
    )


@app.command("add")
def profile_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile name, e.g. prod."),
    url: str = typer.Option(..., "--url", help="API base URL; `/api/v1` is appended if absent."),
    bu: str | None = typer.Option(None, "--bu", help="Business-unit slug sent as X-Business-Unit."),
    default: bool = typer.Option(False, "--default", help="Make this the default profile."),
) -> None:
    """Create or replace a profile in config.toml."""
    path = save_profile(name, url, bu, default)
    ok(f"Saved profile '{name}' to {path}.")
    # Don't tell people to pass --profile when the profile they just saved is
    # already the default — the first profile always is.
    from ..config import default_profile_name

    if default_profile_name() == name:
        echo("[dim]Next: tdt login[/dim]")
    else:
        echo(f"[dim]Next: tdt --profile {name} login[/dim]")
