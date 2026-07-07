"""policies — BU-scoped OPA/conftest rego rules + append-only version history.

Adds:
  - `policies`         : one rego document per rule (see app/models/policy.py),
                         BU-scoped, with per-policy `severity` and `enabled`.
  - `policy_versions`  : immutable snapshot per create/edit/restore.
  - `runs.policy_status`: the OPA gate outcome stamped by the executor.

Purely additive — no existing table or behavior changes. The gate stays off by
default (per-BU `opa.mode` defaults to `off` in the Config table), so existing
runs are unaffected until an admin opts in.

Revision ID: 036_policies
Revises: 035_inventory_ignore_rules
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "036_policies"
down_revision = "035_inventory_ignore_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("business_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("rego", sa.Text(), nullable=False),
        sa.Column("tests_rego", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=8), nullable=False, server_default="block"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("business_unit_id", "name", name="uq_policies_bu_name"),
    )
    op.create_index("ix_policies_bu", "policies", ["business_unit_id"])

    op.create_table(
        "policy_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "policy_id",
            sa.String(),
            sa.ForeignKey("policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("rego", sa.Text(), nullable=False),
        sa.Column("tests_rego", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "policy_id", "version", name="uq_policy_versions_policy_version"
        ),
    )
    op.create_index("ix_policy_versions_policy", "policy_versions", ["policy_id"])

    op.add_column(
        "runs",
        sa.Column(
            "policy_status",
            sa.String(length=16),
            nullable=False,
            server_default="not_run",
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "policy_status")
    op.drop_index("ix_policy_versions_policy", table_name="policy_versions")
    op.drop_table("policy_versions")
    op.drop_index("ix_policies_bu", table_name="policies")
    op.drop_table("policies")
