"""APIKey — long-lived, scoped credential for automation.

An API key authenticates as its owning user but carries its own permission
rider that is *narrower* than (and independent of) the owner's interactive
session:

  - business_unit_id  — the single BU the key may act in (tenancy is forced to
    this BU regardless of any X-Business-Unit header the client sends).
  - capability         — one of read | plan | apply (ascending). `read` is
    viewer-equivalent, `plan` may trigger plan-only runs, `apply` may also
    approve/apply.
  - workspace_ids      — optional allowlist (JSON array). Empty/None means
    "any workspace in the BU"; otherwise the key may only touch the listed
    workspaces.

The plaintext token (`tdt_<random>`) is shown to the admin exactly once at
creation; we persist only its SHA-256 hash (`token_hash`) plus a short display
prefix (`token_prefix`). Keys are soft-revoked via `revoked_at` and may carry
an optional `expires_at`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Short, non-secret display fragment, e.g. "tdt_ab12cd…". Safe to show in lists.
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 hex of the full plaintext token. Unique so a lookup is an indexed
    # exact match. We never store the plaintext.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Owning user — the key authenticates AS this user.
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The single BU the key is bound to. Tenancy is forced to this BU.
    business_unit_id: Mapped[str] = mapped_column(
        String, ForeignKey("business_units.id", ondelete="CASCADE"), nullable=False
    )
    # read | plan | apply
    capability: Mapped[str] = mapped_column(String(16), nullable=False, default="read")
    # Optional workspace allowlist (JSON array of workspace ids). NULL/empty = all in BU.
    workspace_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # NULL = never expires.
    expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Admin who minted the key (for audit / display).
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Bumped on every successful authentication (best-effort).
    last_used_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Soft-revoke. Non-NULL = revoked and no longer usable.
    revoked_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
