"""environment links + pairs (Governance › Environments)

Adds `env_links` (a saved source-node → target-node relation with its
rewrite / override / protected rules) and `env_pairs` (the materialised
source-stack ↔ target-stack pairing of a link). Promotions, their runs and the
compare cache land in later revisions with the code that uses them.

Pair stack ids are deliberately *not* FKs to `workspaces`: pairs are
re-derived from the live workspace set on every reconcile, and untracking a
workspace must not be blocked (or silently cascade) by a link that mentions it.

Revision ID: 045_env_links
Revises: 044_drift_reports_index
"""
import sqlalchemy as sa
from alembic import op

revision = "045_env_links"
down_revision = "044_drift_reports_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "env_links",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("business_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("engine", sa.String(20), nullable=False, server_default="terraform"),
        sa.Column("source_node", sa.JSON(), nullable=False),
        sa.Column("target_node", sa.JSON(), nullable=False),
        sa.Column("rewrite_rules", sa.JSON(), nullable=False),
        sa.Column("pair_overrides", sa.JSON(), nullable=False),
        sa.Column("protected_rules", sa.JSON(), nullable=False),
        sa.Column("rules_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("business_unit_id", "name", name="uq_env_links_bu_name"),
    )
    op.create_index("ix_env_links_business_unit_id", "env_links", ["business_unit_id"])

    op.create_table(
        "env_pairs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "link_id",
            sa.String(),
            sa.ForeignKey("env_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pair_key", sa.String(1100), nullable=False),
        sa.Column("relative_path", sa.String(500), nullable=False, server_default=""),
        sa.Column("source_rel", sa.String(500), nullable=True),
        sa.Column("target_rel", sa.String(500), nullable=True),
        sa.Column("source_stack_id", sa.String(), nullable=True),
        sa.Column("target_stack_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="not_compared"),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("proposed_target_rel", sa.String(500), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("cache_key", sa.String(200), nullable=True),
        sa.Column("last_compared_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("link_id", "pair_key", name="uq_env_pairs_link_key"),
    )
    op.create_index("ix_env_pairs_link_id", "env_pairs", ["link_id"])
    op.create_index("ix_env_pairs_source_stack_id", "env_pairs", ["source_stack_id"])
    op.create_index("ix_env_pairs_target_stack_id", "env_pairs", ["target_stack_id"])


def downgrade() -> None:
    op.drop_index("ix_env_pairs_target_stack_id", table_name="env_pairs")
    op.drop_index("ix_env_pairs_source_stack_id", table_name="env_pairs")
    op.drop_index("ix_env_pairs_link_id", table_name="env_pairs")
    op.drop_table("env_pairs")
    op.drop_index("ix_env_links_business_unit_id", table_name="env_links")
    op.drop_table("env_links")
