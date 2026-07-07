"""global_variables.business_unit_id — scope global vars per Business Unit.

Global variables were org-wide (no BU column, globally-unique key), so they
leaked across BUs — including into every run's TF_VAR_* via get_merged_for_run.
This scopes them per BU. Backfill DUPLICATES each existing global var into every
BU so no BU loses its current globals while future edits become isolated.

Revision ID: 031_global_vars_bu_scope
Revises: 030_cluster_aws_account
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "031_global_vars_bu_scope"
down_revision = "030_cluster_aws_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the column (nullable for the backfill window).
    op.add_column(
        "global_variables", sa.Column("business_unit_id", sa.String(), nullable=True)
    )

    # 2. Drop the OLD unique(key) constraint BEFORE the backfill. The backfill
    #    duplicates each key once PER BU, so with >1 BU there are intentionally
    #    multiple rows with the same key — which would violate unique(key) if it
    #    were still in place. (This is the fix for the prod deploy failure:
    #    "duplicate key value violates unique constraint uq_global_variables_key".)
    op.drop_constraint("uq_global_variables_key", "global_variables", type_="unique")

    # 3. Backfill: duplicate each existing (BU-less) global var into EVERY BU,
    #    then drop the originals. Net: one row per (BU, original key). PG16 has
    #    gen_random_uuid() built in.
    op.execute(
        """
        INSERT INTO global_variables
            (id, business_unit_id, key, value_encrypted, is_secret, is_hcl,
             description, created_at, updated_at)
        SELECT gen_random_uuid(), bu.id, gv.key, gv.value_encrypted, gv.is_secret,
               gv.is_hcl, gv.description, now(), now()
        FROM global_variables gv
        CROSS JOIN business_units bu
        WHERE gv.business_unit_id IS NULL
        """
    )
    op.execute("DELETE FROM global_variables WHERE business_unit_id IS NULL")

    # 4. Add the new per-BU unique constraint (now satisfied — each (bu, key)
    #    pair is distinct).
    op.create_unique_constraint(
        "uq_global_variables_bu_key", "global_variables", ["business_unit_id", "key"]
    )

    # 5. Lock it down + index the FK-ish column.
    op.alter_column("global_variables", "business_unit_id", nullable=False)
    op.create_index(
        "ix_global_variables_bu", "global_variables", ["business_unit_id"]
    )


def downgrade() -> None:
    # Best-effort reverse (not run in prod). Note: if multiple BUs share a key,
    # re-adding the global key constraint will fail — acceptable for a downgrade.
    op.drop_index("ix_global_variables_bu", table_name="global_variables")
    op.drop_constraint("uq_global_variables_bu_key", "global_variables", type_="unique")
    op.alter_column("global_variables", "business_unit_id", nullable=True)
    op.create_unique_constraint(
        "uq_global_variables_key", "global_variables", ["key"]
    )
    op.drop_column("global_variables", "business_unit_id")
