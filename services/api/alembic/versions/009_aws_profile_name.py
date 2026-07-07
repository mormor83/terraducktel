"""Add aws_profile_name to aws_accounts

Some terraform stacks pin the AWS provider to a named profile (e.g.,
`provider "aws" { profile = "devops" }`). When that's the case the executor
must write `~/.aws/credentials` with that profile section so the SDK doesn't
fall through to "shared config profile X not found".

Revision ID: 009_aws_profile_name
Revises: 008_rebrand_salt
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = "009_aws_profile_name"
down_revision = "008_rebrand_salt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "aws_accounts",
        sa.Column("aws_profile_name", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aws_accounts", "aws_profile_name")
