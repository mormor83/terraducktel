"""The object every command reads its connection + formatting choice from."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import credentials as creds
from .client import Client
from .config import Profile, resolve
from .output import Fmt


@dataclass
class AppCtx:
    profile_name: str | None = None
    url: str | None = None
    bu: str | None = None
    fmt: Fmt = Fmt.table
    _client: Client | None = field(default=None, repr=False)
    _profile: Profile | None = field(default=None, repr=False)

    @property
    def profile(self) -> Profile:
        """Resolved lazily so `tdt profile add` works before any config exists."""
        if self._profile is None:
            self._profile = resolve(self.profile_name, self.url, self.bu)
        return self._profile

    @property
    def client(self) -> Client:
        if self._client is None:
            prof = self.profile
            self._client = Client(prof, creds.load(prof.name))
        return self._client
