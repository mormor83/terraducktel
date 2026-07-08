"""Backend-neutral Terraform state object store.

The API is the only component that persists Terraform state; the executor
always talks to the HTTP state backend (`routers/state.py`) and never learns
which object store sits behind it. This Protocol is the contract every backend
must satisfy so `state.py`'s HTTP-status mapping stays correct regardless of
whether state lives in S3, Azure Blob, or GCS.
"""
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class StateStore(Protocol):
    """Object-store contract for Terraform state.

    Every implementation MUST honor this so `routers/state.py` keeps its
    404-vs-503 (GET) and 500 (POST) invariants:

    - ``get_state_at``: return the state bytes, or ``None`` *iff* the object
      does not exist. Any other failure (auth, network, misconfig) MUST raise
      — the GET handler maps a raised exception to 503, but ``None`` to a
      legitimate 404 ("no state yet").
    - ``delete_state_at``: return ``True`` on a successful delete OR when the
      object is already absent; raise on anything else.
    - ``put_state_at``: persist the bytes; raise on failure (→ 500).

    Encryption-at-rest and endpoint/LocalStack handling are backend-internal
    and MUST NOT appear in this surface.
    """

    def get_state_at(self, key: str) -> Optional[bytes]: ...

    def put_state_at(self, key: str, state_bytes: bytes) -> None: ...

    def delete_state_at(self, key: str) -> bool: ...
