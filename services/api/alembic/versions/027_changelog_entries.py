"""changelog_entries — TDT-owned changelog (synced GitHub PRs + manual rows).

Backs the Settings → Changelog tab so the UI reads from TDT instead of hitting
GitHub on every page load. `github` rows are upserted by an explicit Sync
(keyed on the PR number in `ref`); `manual` rows are admin-authored (ref NULL).

Revision ID: 027_changelog_entries
Revises: 026_workspace_azure_subscription
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "027_changelog_entries"
down_revision = "026_workspace_azure_subscription"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "changelog_entries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("entry_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "business_unit_id", "source", "ref", name="uq_changelog_bu_source_ref"
        ),
    )
    op.create_index(
        "ix_changelog_entries_bu", "changelog_entries", ["business_unit_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_changelog_entries_bu", table_name="changelog_entries")
    op.drop_table("changelog_entries")
