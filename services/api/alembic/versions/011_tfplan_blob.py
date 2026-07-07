"""Add tfplan_b64 to runs for two-phase plan→apply.

Revision ID: 011_tfplan_blob
Revises: 010_run_plan_json
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = "011_tfplan_blob"
down_revision = "010_run_plan_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("tfplan_b64", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "tfplan_b64")
