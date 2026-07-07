"""Workspace state-key column + path-based uniqueness.

Phase 2 of the discovery rename. Existing workspaces keep their joined names
(e.g. `cust01-worker-queue-consumer`) and their S3 state-key, because the
state file is already at `tfstate/{account}/{region}/{env}/{name}/...` and
renaming would orphan it. New imports get `name = leaf-only` and a
`state_key` column that encodes the unique path — the Workspace model's
`state_path` property uses it when set, else falls back to the legacy formula.

Changes:
  - workspaces.state_key  text NULL  — explicit S3 state subkey for new imports.
                                       NULL on legacy rows ⇒ use the
                                       `{account}/{region}/{env}/{name}` formula.
  - drop  uq_workspaces_bu_acc_region_env_name
  - add   uq_workspaces_bu_acc_region_env_path
          on (business_unit_id, aws_account_id, region, environment, tf_working_dir)
    Path is the canonical identity now; `name` is just a display label.

Revision ID: 019_workspace_state_key
Revises: 018_business_units
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa

revision = "019_workspace_state_key"
down_revision = "018_business_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("state_key", sa.Text(), nullable=True),
    )

    op.drop_constraint(
        "uq_workspaces_bu_acc_region_env_name", "workspaces", type_="unique"
    )
    op.create_unique_constraint(
        "uq_workspaces_bu_acc_region_env_path",
        "workspaces",
        ["business_unit_id", "aws_account_id", "region", "environment", "tf_working_dir"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workspaces_bu_acc_region_env_path", "workspaces", type_="unique"
    )
    op.create_unique_constraint(
        "uq_workspaces_bu_acc_region_env_name",
        "workspaces",
        ["business_unit_id", "aws_account_id", "region", "environment", "name"],
    )
    op.drop_column("workspaces", "state_key")
