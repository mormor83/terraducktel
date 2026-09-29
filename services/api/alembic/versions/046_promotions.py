"""promotions, compare cache, background jobs

Adds the rest of Governance › Environments:
  - `env_compare_results` — latest config/state compare per (pair, direction),
    keyed by a cache key over both commits, both state serials and the link's
    rules_version.
  - `promotions` + `promotion_runs` — one push of selected differences and the
    ordinary runs it created (one per affected target stack).
  - `bg_jobs` — tiny generic queue for API-side compare / verify work, claimed
    with SKIP LOCKED by an in-process loop like the run worker.
  - `runs.promotion_id` — nullable tag on runs a promotion created. Promotion
    runs are plain apply runs through the normal pipeline; this is display +
    status derivation only.

Revision ID: 046_promotions
Revises: 045_env_links
"""
import sqlalchemy as sa
from alembic import op

revision = "046_promotions"
down_revision = "045_env_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "env_compare_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "pair_id", sa.String(),
            sa.ForeignKey("env_pairs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("direction", sa.String(10), nullable=False, server_default="forward"),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("refs", sa.JSON(), nullable=True),
        sa.Column("config_diff", sa.JSON(), nullable=True),
        sa.Column("state_diff", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("pair_id", "direction", name="uq_env_compare_pair_dir"),
    )
    op.create_index("ix_env_compare_results_pair_id", "env_compare_results", ["pair_id"])

    op.create_table(
        "promotions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "link_id", sa.String(),
            sa.ForeignKey("env_links.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="promote"),
        sa.Column("reverts_promotion_id", sa.String(), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False, server_default="forward"),
        sa.Column("initiated_by", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("selection_hash", sa.String(64), nullable=False),
        sa.Column("commit_message", sa.Text(), nullable=True),
        sa.Column("commits", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_promotions_number", "promotions", ["number"], unique=True)
    op.create_index("ix_promotions_link_id", "promotions", ["link_id"])
    op.create_index("ix_promotions_business_unit_id", "promotions", ["business_unit_id"])
    op.create_index("ix_promotions_selection_hash", "promotions", ["selection_hash"])

    op.create_table(
        "promotion_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "promotion_id", sa.String(),
            sa.ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("pair_id", sa.String(), nullable=True),
        sa.Column("target_stack_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("created_stack", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("residual", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_promotion_runs_promotion_id", "promotion_runs", ["promotion_id"])
    op.create_index("ix_promotion_runs_target_stack_id", "promotion_runs", ["target_stack_id"])
    op.create_index("ix_promotion_runs_run_id", "promotion_runs", ["run_id"])

    op.create_table(
        "bg_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bg_jobs_state", "bg_jobs", ["state"])
    op.create_index("ix_bg_jobs_dedupe_key", "bg_jobs", ["dedupe_key"])

    op.add_column("runs", sa.Column("promotion_id", sa.String(), nullable=True))
    op.create_index("ix_runs_promotion_id", "runs", ["promotion_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_promotion_id", table_name="runs")
    op.drop_column("runs", "promotion_id")
    op.drop_index("ix_bg_jobs_dedupe_key", table_name="bg_jobs")
    op.drop_index("ix_bg_jobs_state", table_name="bg_jobs")
    op.drop_table("bg_jobs")
    op.drop_index("ix_promotion_runs_run_id", table_name="promotion_runs")
    op.drop_index("ix_promotion_runs_target_stack_id", table_name="promotion_runs")
    op.drop_index("ix_promotion_runs_promotion_id", table_name="promotion_runs")
    op.drop_table("promotion_runs")
    op.drop_index("ix_promotions_selection_hash", table_name="promotions")
    op.drop_index("ix_promotions_business_unit_id", table_name="promotions")
    op.drop_index("ix_promotions_link_id", table_name="promotions")
    op.drop_index("ix_promotions_number", table_name="promotions")
    op.drop_table("promotions")
    op.drop_index("ix_env_compare_results_pair_id", table_name="env_compare_results")
    op.drop_table("env_compare_results")
