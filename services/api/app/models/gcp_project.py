"""GcpProject model — one row per GCP project onboarded to TDT.

Mirrors AzureSubscription / AwsAccount: same encryption scheme (a distinct
HKDF salt so a GCP service-account key can't be confused with an AWS or Azure
secret), same per-BU uniqueness story, same "credentials never leave the API"
rule. Used by workspaces that target the `google` / `google-beta` provider; the
executor reads the SA JSON and writes it to a 0600 file, exporting the standard
`GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_PROJECT` env vars that terraform's
Google provider already understands.

State backends:
  - When a workspace sets `state_backend=gcs`, its Terraform state is stored in
    the `state_bucket` here (reusing this same SA key). Otherwise GCP workspaces
    fall back to S3, exactly like Azure workspaces do.
"""
import uuid

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GcpProject(Base):
    __tablename__ = "gcp_projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Owning Business Unit. Same per-BU uniqueness story as aws_accounts.
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # GCP project id — the natural key within a BU, e.g. "acme-prod-1234".
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Service-account email parsed from the uploaded key, for display/audit.
    client_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Human-readable display name (e.g. "acme-prod").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_region: Mapped[str] = mapped_column(String(50), nullable=False, default="us-central1")
    # Optional GCS bucket for `state_backend=gcs` workspaces. Nullable so a
    # provider-only GCP project (state still in S3) validates fine.
    state_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state_prefix: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Encrypted service-account JSON key. NEVER logged, NEVER returned in responses.
    service_account_json_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
