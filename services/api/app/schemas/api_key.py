"""Pydantic schemas for API keys.

Secrets never leave the API after creation: `APIKeyResponse` carries only the
non-secret display prefix. `APIKeyCreateResponse` is the *one* place the
plaintext token is ever returned (immediately after minting).
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Capability = Literal["read", "plan", "apply", "admin"]


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # read < plan < apply < admin. `admin` keys act with the full admin role of
    # their owner within their BU (create/discover/update workspaces, manage AWS
    # accounts, policies, integrations, drift, …) but can never touch *identity*
    # (minting keys, user management, BU CRUD) — see api_key_service.
    capability: Capability = "read"
    # Optional workspace allowlist (workspace ids). Empty/omitted = any
    # workspace in the key's BU.
    workspace_ids: Optional[list[str]] = None
    # Optional expiry. Omit for a non-expiring key.
    expires_at: Optional[datetime] = None


DEFAULT_OVERLAP_HOURS = 24
MAX_OVERLAP_HOURS = 24 * 7


class APIKeyRotate(BaseModel):
    """Body for `POST /api-keys/{id}/rotate`."""

    # How long the OLD secret keeps working after the new one is minted. The
    # point of the whole feature: you cannot atomically swap a credential that
    # lives in a CI secret store, a laptop keychain and a cron job at once.
    # 0 means immediate cutover, which is what `regenerate` does in place.
    overlap_hours: int = Field(
        default=DEFAULT_OVERLAP_HOURS, ge=0, le=MAX_OVERLAP_HOURS
    )


class APIKeyResponse(BaseModel):
    id: str
    name: str
    token_prefix: str
    capability: Capability
    workspace_ids: Optional[list[str]] = None
    business_unit_id: str
    user_id: str
    created_by: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    # Set when this key was superseded by a rotation; `expires_at` then marks
    # the end of its overlap window.
    rotated_at: Optional[datetime] = None
    superseded_by_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class APIKeyCreateResponse(APIKeyResponse):
    """Returned exactly once on creation — includes the plaintext token."""

    token: str


class APIKeyRotateResponse(APIKeyCreateResponse):
    """The successor key, plus what happens to the one it replaced.

    Returned once, like creation. `predecessor_expires_at` is the deadline for
    swapping the old secret out of wherever it lives; after it passes the old
    key stops authenticating on its own, with no second call needed.
    """

    predecessor_id: str
    predecessor_expires_at: Optional[datetime] = None
