"""BusinessUnit + UserBusinessUnit membership.

A Business Unit is a logical tenant in Terraducktel. Each BU owns its own AWS
accounts, GitHub integration (PAT, source repo, modules repo, webhook secret —
all namespaced in the `config` table under `bu.<slug>.*`), and workspaces.

`is_superadmin=true` users on the `users` table see all BUs and bypass the
per-BU role check. Everyone else is a member of one or more BUs through
`user_business_units` with a per-BU `role` (operator | viewer).
"""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, String, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# UUID of the seeded 'default' BU — matches migration 018. Used by the seed
# script and by any backfill helpers.
DEFAULT_BU_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_BU_SLUG = "default"


class BusinessUnit(Base):
    __tablename__ = "business_units"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Immutable URL-safe identifier. Referenced by config keys like
    # `bu.<slug>.github.token`, so renaming would orphan secrets.
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserBusinessUnit(Base):
    __tablename__ = "user_business_units"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    business_unit_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("business_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # operator | viewer. Admins are global via users.is_superadmin.
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
