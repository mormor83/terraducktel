"""Exit codes and the exception the CLI turns into them.

The codes are a **contract**, inherited unchanged from `tdt-flow.sh` so existing
wrappers keep working and an agent can branch on status instead of parsing prose.
Never renumber these.
"""
from __future__ import annotations


class ExitCode:
    OK = 0
    USAGE = 1          # bad args / missing config
    AUTH = 2           # 401/403, no stored credentials, expired refresh
    PLAN_FAILED = 3    # the plan phase failed (terraform error, checkov, OPA)
    REJECTED = 4       # human rejected, or an --approve-if / --max-destroy gate refused
    APPLY_FAILED = 5   # the apply phase failed
    TIMEOUT = 6        # gave up waiting for a state transition
    API = 7            # anything else the API said no to (404, 409, 5xx, transport)


class TdtError(Exception):
    """A user-facing failure. `code` becomes the process exit status."""

    def __init__(self, message: str, code: int = ExitCode.API, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
