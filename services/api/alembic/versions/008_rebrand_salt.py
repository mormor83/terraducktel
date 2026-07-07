"""Wipe legacy encrypted rows and rebrand the HKDF salt to terraducktel.

Rationale: every encrypted row in the DB was derived under a legacy HKDF salt.
On the next boot the salt becomes b"terraducktel-config-v1", which means existing
ciphertext can no longer be decrypted. We DROP those rows here so the app
starts clean and the operator re-enters AWS credentials via the AWS Accounts
page (the only secrets that should live in the DB).

What this migration deletes:
  - aws_accounts            : forces re-entry of access key / secret access key
  - config WHERE is_secret  : Slack webhook URL, SMTP password, etc.
  - config_history          : fingerprints + plaintext-config audit rows; the
                              audit trail referenced rows we're about to drop,
                              so keeping the history would create dangling refs.

Non-secret config rows (is_secret = false) are PRESERVED.

Revision ID: 008_rebrand_salt
Revises: 007_run_steps
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "008_rebrand_salt"
down_revision = "007_run_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM aws_accounts")
    op.execute("DELETE FROM config_history")
    op.execute("DELETE FROM config WHERE is_secret = TRUE")


def downgrade() -> None:
    # Irreversible — encrypted rows derived under the old salt are gone.
    pass
