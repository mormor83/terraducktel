"""Changelog entries stored in TDT.

The Settings → Changelog tab reads from this table (not GitHub live), so the
page loads instantly and survives a missing/expired GitHub token or a GitHub
outage. Rows come from two sources:

- `github`: populated by an explicit **Sync** that pulls merged pull requests
  from the BU's configured `changelog.repo` and upserts them keyed on the PR
  number (`ref`). Re-syncing updates titles/bodies in place rather than
  duplicating.
- `manual`: authored by an admin directly in the UI (`ref` is NULL).

BU-scoped via `business_unit_id` (a logical column, no enforced FK — same
convention as `workspaces.business_unit_id`).
"""
import uuid

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChangelogEntry(Base):
    __tablename__ = "changelog_entries"
    __table_args__ = (
        # Re-sync upserts a github PR by (bu, source, ref) instead of inserting
        # a dupe. Manual rows have ref=NULL; Postgres allows multiple NULLs, so
        # they're never collapsed by this constraint.
        UniqueConstraint("business_unit_id", "source", "ref", name="uq_changelog_bu_source_ref"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # "github" | "manual"
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # PR number for github entries (e.g. "30"); NULL for manual entries.
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # merged_at for github / admin-chosen-or-now for manual; primary sort key.
    entry_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
