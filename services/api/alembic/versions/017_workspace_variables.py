"""Workspace + global variables for terraform runs.

Adds three things:

  - `global_variables`         : org-wide TF_VAR_* defaults (admin write)
  - `workspace_variables`      : per-workspace overrides (operator+ write)
  - `runs.variables_encrypted` : per-run additions/overrides, encoded as a
                                 Fernet-encrypted JSON blob on the run row

Merge precedence at executor launch is `global ← workspace ← run`; last wins
per key. All `value_encrypted` columns store ciphertext via the same Fernet
scheme used by `aws_accounts` / `ConfigService` (HKDF over CREDENTIAL_ENCRYPTION_KEY).

Revision ID: 017_workspace_variables
Revises: 016_audit_chain_link_only
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = "017_workspace_variables"
down_revision = "016_audit_chain_link_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "global_variables",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_hcl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key", name="uq_global_variables_key"),
    )

    op.create_table(
        "workspace_variables",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_hcl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "key", name="uq_workspace_variables_workspace_key"),
    )
    op.create_index(
        "ix_workspace_variables_workspace_id",
        "workspace_variables",
        ["workspace_id"],
    )

    # Per-run overrides — opaque ciphertext blob (JSON object encrypted as one
    # Fernet token). Decrypted once at executor launch, merged onto the global
    # + workspace layers, and replayed verbatim on the apply phase after a
    # 4-eyes approval. Nullable for runs that supplied none.
    op.add_column(
        "runs",
        sa.Column("variables_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "variables_encrypted")
    op.drop_index("ix_workspace_variables_workspace_id", table_name="workspace_variables")
    op.drop_table("workspace_variables")
    op.drop_table("global_variables")
