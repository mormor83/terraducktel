"""Add plan_json to runs for the visualization canvas.

Revision ID: 010_run_plan_json
Revises: 009_aws_profile_name
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = "010_run_plan_json"
down_revision = "009_aws_profile_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("plan_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "plan_json")
