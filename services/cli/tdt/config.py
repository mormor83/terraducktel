"""Profile config: `~/.config/tdt/config.toml`.

A *profile* bundles the two things every TDT request needs — the API base URL
and the business-unit slug — so they stop being three env vars you re-export in
every shell.

```toml
default_profile = "prod"

[profiles.prod]
url = "https://terraducktel.example.com/api/v1"
bu  = "home"

[profiles.local]
url = "http://localhost:8001/api/v1"
bu  = "default"
```

Resolution order for each field, first hit wins:
  1. an explicit CLI flag (`--url` / `--bu`)
  2. the env var (`TDT_API_URL` / `TDT_BU`) — kept for `tdt-flow.sh` parity
  3. the selected profile in config.toml
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ExitCode, TdtError

DEFAULT_LOCAL_URL = "http://localhost:8001/api/v1"


def config_dir() -> Path:
    """`$TDT_CONFIG_DIR`, else `$XDG_CONFIG_HOME/tdt`, else `~/.config/tdt`."""
    override = os.environ.get("TDT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "tdt"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Profile:
    name: str
    url: str
    bu: str | None


def _load_raw() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise TdtError(f"Could not read {path}: {exc}", ExitCode.USAGE) from exc


def list_profiles() -> dict[str, dict]:
    return dict(_load_raw().get("profiles") or {})


def default_profile_name() -> str | None:
    raw = _load_raw()
    explicit = raw.get("default_profile")
    if explicit:
        return str(explicit)
    profiles = raw.get("profiles") or {}
    # A single configured profile is unambiguous — don't make the user name it.
    if len(profiles) == 1:
        return next(iter(profiles))
    return None


def _normalize_url(url: str) -> str:
    """Accept a bare host and append the versioned prefix the API lives under."""
    url = url.rstrip("/")
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url


def resolve(
    profile: str | None = None,
    url: str | None = None,
    bu: str | None = None,
) -> Profile:
    """Merge flags, env and config.toml into the profile a command should use."""
    raw = _load_raw()
    profiles = raw.get("profiles") or {}

    name = profile or os.environ.get("TDT_PROFILE") or default_profile_name()
    entry: dict = {}
    if name:
        if name not in profiles and (url or os.environ.get("TDT_API_URL")):
            # Named a profile that isn't in config, but the URL came from a flag
            # or env — treat the name as a label rather than erroring out.
            entry = {}
        elif name not in profiles:
            known = ", ".join(sorted(profiles)) or "none configured"
            raise TdtError(
                f"Unknown profile '{name}' (known: {known})",
                ExitCode.USAGE,
                hint="Add one with: tdt profile add <name> --url <url> --bu <slug>",
            )
        else:
            entry = dict(profiles[name])

    final_url = url or os.environ.get("TDT_API_URL") or entry.get("url")
    if not final_url:
        raise TdtError(
            "No API URL configured.",
            ExitCode.USAGE,
            hint=(
                "Set one up once:\n"
                "  tdt profile add prod --url https://tdt.example.com --bu home\n"
                "  tdt login\n"
                "Or for a one-off, note that --url is a global option and may go\n"
                "either side of the command:\n"
                "  tdt --url https://tdt.example.com --bu home ws list\n"
                f"Or export TDT_API_URL={DEFAULT_LOCAL_URL}"
            ),
        )

    final_bu = bu or os.environ.get("TDT_BU") or entry.get("bu")
    return Profile(name=name or "(ad-hoc)", url=_normalize_url(str(final_url)), bu=final_bu)


def save_profile(name: str, url: str, bu: str | None, make_default: bool) -> Path:
    """Write/replace one profile. Hand-rolled TOML — the file stays tiny and
    round-tripping it with a full writer isn't worth another dependency."""
    raw = _load_raw()
    profiles = dict(raw.get("profiles") or {})
    profiles[name] = {"url": _normalize_url(url), **({"bu": bu} if bu else {})}
    default = name if make_default else raw.get("default_profile") or name

    lines = [f'default_profile = "{default}"', ""]
    for pname in sorted(profiles):
        lines.append(f"[profiles.{pname}]")
        lines.append(f'url = "{profiles[pname]["url"]}"')
        if profiles[pname].get("bu"):
            lines.append(f'bu = "{profiles[pname]["bu"]}"')
        lines.append("")

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path
