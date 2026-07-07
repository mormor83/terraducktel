"""AzureSubscription model — one row per Azure subscription onboarded to TDT.

Mirrors AwsAccount: same encryption scheme, same per-BU uniqueness story,
same "credentials never leave the API" rule. Used by workspaces that
target the `azurerm` provider; the executor reads the SP creds and
exports them as the standard `ARM_*` env vars that `terraform`'s Azure
provider already understands.

State backends:
  - Terraform state for Azure workspaces still lives in S3 (we do not run
    a parallel Azure Storage state backend for this release). The
    workspace's `state_aws_account_id` (or `aws_account_id` if the
    workspace is also AWS-linked) tells the executor which AWS creds to
    use for the S3 backend; the SP creds here are only used by the
    `azurerm` provider itself.
"""
import uuid

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AzureSubscription(Base):
    __tablename__ = "azure_subscriptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Owning Business Unit. Same per-BU uniqueness story as aws_accounts.
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # UUIDs from Azure — kept as strings for portability; format enforced at
    # the schema layer rather than in the DB so re-imports stay flexible.
    subscription_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Service-principal application (client) ID — paired with the encrypted
    # secret below. We standardise on SP auth for now; managed-identity and
    # OIDC federation can land later without a schema break.
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Human-readable display name (e.g. "acme-prod").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_location: Mapped[str] = mapped_column(String(50), nullable=False, default="eastus")
    # Encrypted SP secret. NEVER logged, NEVER returned in API responses.
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
