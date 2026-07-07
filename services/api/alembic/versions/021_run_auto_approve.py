"""runs.auto_approve_if_no_changes / runs.auto_approve_skip_apply

Adds two opt-in flags to a run, set at run-create time:

  - auto_approve_if_no_changes — when true, a successful plan with
    summary 0/0/0 (and all gates green) is approved by the system
    instead of pausing for a human. The audit entry records
    "auto-approved: plan 0/0/0".
  - auto_approve_skip_apply — when true together with the flag above
    on a 0/0/0 auto-approval, the apply phase is skipped entirely and
    the run is marked succeeded. When false, a no-op apply is still
    executed so the audit trail looks identical to a normal run.

Both default to false so existing automation is unchanged.

Revision ID: 021_run_auto_approve
Revises: 020_user_display_name
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "021_run_auto_approve"
down_revision = "020_user_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "auto_approve_if_no_changes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "auto_approve_skip_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "auto_approve_skip_apply")
    op.drop_column("runs", "auto_approve_if_no_changes")
