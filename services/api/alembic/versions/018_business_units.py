"""Multi-tenant Business Units.

Carves the global system into N BUs. Each BU is a logical container for AWS
accounts, GitHub integration, and workspaces. Existing data is backfilled to a
single 'default' BU so this migration is non-breaking — the platform behaves
identically until a second BU is created.

Changes:
  - new table  business_units(id, slug, name, ...)
  - seed row   ('default', 'Default')
  - users      + is_superadmin  (backfilled from role='admin')
  - new table  user_business_units(user_id, business_unit_id, role)
               backfilled with each non-admin user's current role on 'default'
  - aws_accounts  + business_unit_id  (FK, backfilled to 'default', NOT NULL)
                  drop global unique on account_id; replace with composite
                  unique on (business_unit_id, account_id)
  - workspaces    + business_unit_id  (FK, backfilled to 'default', NOT NULL)
                  add unique (business_unit_id, name)

The `users.role` column is left in place for one release as a fallback so
existing code paths keep working until per-BU role resolution lands. It will
be dropped in a follow-up migration.

Revision ID: 018_business_units
Revises: 017_workspace_variables
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = "018_business_units"
down_revision = "017_workspace_variables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- business_units ----------------------------------------------------
    op.create_table(
        "business_units",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
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
        sa.UniqueConstraint("slug", name="uq_business_units_slug"),
    )

    # Seed the default BU so backfill below has a target.
    op.execute(
        """
        INSERT INTO business_units (id, slug, name)
        VALUES ('00000000-0000-0000-0000-000000000001', 'default', 'Default')
        """
    )

    # ---- users.is_superadmin ----------------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute("UPDATE users SET is_superadmin = TRUE WHERE role = 'admin'")

    # ---- user_business_units (memberships) --------------------------------
    op.create_table(
        "user_business_units",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("business_units.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # operator | viewer. Admin is global (is_superadmin), not a per-BU role.
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        """
        INSERT INTO user_business_units (user_id, business_unit_id, role)
        SELECT id,
               '00000000-0000-0000-0000-000000000001',
               role
        FROM users
        WHERE role IN ('operator', 'viewer')
        """
    )

    # ---- aws_accounts.business_unit_id ------------------------------------
    op.add_column(
        "aws_accounts",
        sa.Column("business_unit_id", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE aws_accounts "
        "SET business_unit_id = '00000000-0000-0000-0000-000000000001'"
    )
    op.alter_column("aws_accounts", "business_unit_id", nullable=False)
    op.create_foreign_key(
        "fk_aws_accounts_business_unit",
        "aws_accounts",
        "business_units",
        ["business_unit_id"],
        ["id"],
    )
    # Account ID was globally unique; loosen to per-BU so two BUs *could*
    # register the same AWS account (discouraged but allowed — see CLAUDE.md).
    # The original constraint was named by SQLAlchemy autonaming convention,
    # which produced `uq_aws_accounts_account_id` on this deployment.
    op.drop_constraint("uq_aws_accounts_account_id", "aws_accounts", type_="unique")
    op.create_unique_constraint(
        "uq_aws_accounts_bu_account_id",
        "aws_accounts",
        ["business_unit_id", "account_id"],
    )

    # ---- workspaces.business_unit_id --------------------------------------
    op.add_column(
        "workspaces",
        sa.Column("business_unit_id", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE workspaces "
        "SET business_unit_id = '00000000-0000-0000-0000-000000000001'"
    )
    op.alter_column("workspaces", "business_unit_id", nullable=False)
    op.create_foreign_key(
        "fk_workspaces_business_unit",
        "workspaces",
        "business_units",
        ["business_unit_id"],
        ["id"],
    )
    # Workspace logical uniqueness is (account, region, environment, name) —
    # two workspaces can share a name as long as they live in different
    # (account, region, env) tuples. Add BU to the front of that tuple so a
    # second BU can register a stack with the same coordinates without
    # colliding. We deliberately do NOT enforce (business_unit_id, name) only
    # — that would reject legitimate duplicates that already exist.
    op.create_unique_constraint(
        "uq_workspaces_bu_acc_region_env_name",
        "workspaces",
        ["business_unit_id", "aws_account_id", "region", "environment", "name"],
    )
    op.create_index(
        "ix_workspaces_business_unit_id",
        "workspaces",
        ["business_unit_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspaces_business_unit_id", table_name="workspaces")
    op.drop_constraint("uq_workspaces_bu_acc_region_env_name", "workspaces", type_="unique")
    op.drop_constraint("fk_workspaces_business_unit", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "business_unit_id")

    op.drop_constraint("uq_aws_accounts_bu_account_id", "aws_accounts", type_="unique")
    op.create_unique_constraint(
        "uq_aws_accounts_account_id", "aws_accounts", ["account_id"]
    )
    op.drop_constraint("fk_aws_accounts_business_unit", "aws_accounts", type_="foreignkey")
    op.drop_column("aws_accounts", "business_unit_id")

    op.drop_table("user_business_units")
    op.drop_column("users", "is_superadmin")
    op.drop_table("business_units")
