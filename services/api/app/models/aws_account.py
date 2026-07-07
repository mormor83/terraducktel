"""AwsAccount model — one row per AWS account onboarded to Terraducktel.

Each account owns:
  - a 12-digit AWS account id (the canonical key)
  - a dedicated S3 bucket for Terraform state (one bucket per account, by user
    convention)
  - access key / secret access key, both encrypted at rest with Fernet (HKDF
    derived from CREDENTIAL_ENCRYPTION_KEY) — same scheme used by ConfigService

The Workspace model references the 12-digit id via `Workspace.aws_account_id`;
nothing here is hardcoded.
"""
import uuid

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AwsAccount(Base):
    __tablename__ = "aws_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Owning Business Unit. Two BUs *can* register the same 12-digit AWS account,
    # though this is discouraged — uniqueness is enforced on (business_unit_id,
    # account_id), not globally.
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # The canonical AWS account number (12 digits). Unique per BU.
    account_id: Mapped[str] = mapped_column(String(12), nullable=False)
    # Human-readable display name (e.g. "example-prod").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dedicated state bucket for this account.
    state_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    state_bucket_region: Mapped[str] = mapped_column(String(50), nullable=False, default="us-east-1")
    # Default region used by the executor when a workspace doesn't override it.
    default_region: Mapped[str] = mapped_column(String(50), nullable=False, default="us-east-1")
    # Optional named AWS profile referenced by the workspace's terraform
    # `provider "aws" { profile = "..." }`. When set, the executor writes
    # `~/.aws/credentials` with that profile section and exports AWS_PROFILE.
    aws_profile_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Encrypted credentials. NEVER logged, NEVER returned in API responses.
    access_key_id_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
