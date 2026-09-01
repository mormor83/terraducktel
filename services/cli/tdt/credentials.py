"""Per-profile credential store: `~/.config/tdt/credentials.json`, mode 0600.

Two kinds of credential live here:

* **A JWT pair** (`access_token` + `refresh_token`). The access token is short
  (minutes); the refresh token is redeemed against `POST /auth/refresh`, which
  returns a *new* pair each time. So an actively-used CLI slides forward
  indefinitely and never asks you to paste a token again.
* **An API key** (`tdt_…`). Long-lived, no refresh, and it forces its own BU —
  the `X-Business-Unit` header is ignored for these. Still the right choice for
  CI, where nobody can complete an interactive login.

The file is never world-readable, and `tdt logout` removes the profile's entry.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import config_dir
from .errors import ExitCode, TdtError


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


@dataclass
class Credential:
    kind: str  # "jwt" | "api_key"
    access_token: str | None = None
    refresh_token: str | None = None
    api_key: str | None = None

    @property
    def bearer(self) -> str:
        tok = self.api_key if self.kind == "api_key" else self.access_token
        if not tok:
            raise TdtError("Stored credential has no token", ExitCode.AUTH)
        return tok


def jwt_claims(token: str) -> dict:
    """Best-effort local decode of a JWT payload — display and expiry only.

    Deliberately does NOT verify the signature: the CLI is not an authority on
    these tokens, it just needs `exp` to decide when to refresh and `email` /
    `role` to print. The API is the only thing that validates them.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001 — an unparseable token is simply opaque
        return {}


def seconds_until_expiry(token: str) -> float | None:
    exp = jwt_claims(token).get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return float(exp) - time.time()


def _read_all() -> dict:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        # A corrupt store should send you to `tdt login`, not crash a run.
        return {}


def _write_all(data: dict) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start — never briefly world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(path, 0o600)


def load(profile: str) -> Credential | None:
    """Env `TDT_TOKEN` wins over the store (parity with `tdt-flow.sh`)."""
    env = os.environ.get("TDT_TOKEN")
    if env:
        env = env.strip()
        if env.lower().startswith("bearer "):
            env = env[7:].strip()
        return (
            Credential(kind="api_key", api_key=env)
            if env.startswith("tdt_")
            else Credential(kind="jwt", access_token=env)
        )
    entry = _read_all().get(profile)
    if not entry:
        return None
    return Credential(
        kind=entry.get("kind", "jwt"),
        access_token=entry.get("access_token"),
        refresh_token=entry.get("refresh_token"),
        api_key=entry.get("api_key"),
    )


def store(profile: str, cred: Credential) -> None:
    data = _read_all()
    data[profile] = {k: v for k, v in asdict(cred).items() if v is not None}
    _write_all(data)


def clear(profile: str) -> bool:
    data = _read_all()
    if profile not in data:
        return False
    del data[profile]
    _write_all(data)
    return True
