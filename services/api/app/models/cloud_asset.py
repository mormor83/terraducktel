"""Firefly-style cloud asset inventory.

One row per discovered cloud resource, classified by its IaC status. The
drift-detector refreshes these on every scan: managed resources (from tfstate)
become `codified`/`drifted`/`ghost`, and live resources absent from any state
become `unmanaged`. The Inventory dashboard aggregates these into a codification
percentage + per-state counts and a filterable asset table.

IaC states (see docs/claude/drift.md):
  codified     — managed by IaC and in sync
  drifted      — managed by IaC but deviated (out-of-band change / config delta)
  ghost        — present in code/state but missing in the cloud
  unmanaged    — exists in the cloud, not in any IaC state
  service_managed — created/owned by an AWS service (EKS, CloudFormation,
                    Karpenter, Auto Scaling, …); Terraform owns the parent, not
                    these children, so they're not "rogue" — detected by tags
  ignored      — unmanaged but intentionally excluded (user ignore rule)
  undetermined — could not be classified / unsupported
"""
import uuid

from sqlalchemy import String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

IAC_STATES = (
    "codified", "drifted", "ghost", "unmanaged", "service_managed", "ignored", "undetermined",
)
# States that count as "tracked by IaC" for the codification metric.
MANAGED_STATES = ("codified", "drifted", "ghost")
# States excluded from the codification base entirely — neither tracked nor
# counted against you (service-owned children + intentionally-ignored noise).
EXCLUDED_STATES = ("ignored", "service_managed")


class CloudAsset(Base):
    __tablename__ = "cloud_assets"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "asset_id", name="uq_cloud_assets_bu_asset"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    business_unit_id: Mapped[str] = mapped_column(
        String, ForeignKey("business_units.id"), nullable=False, index=True
    )
    # Null for unmanaged assets — they belong to an account, not a workspace.
    workspace_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("workspaces.id"), nullable=True, index=True
    )
    # Stable cloud identifier: ARN where available, else the terraform id/address.
    asset_id: Mapped[str] = mapped_column(String, nullable=False)
    # Terraform address (e.g. module.vpc.aws_vpc.main); empty for unmanaged.
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_type: Mapped[str] = mapped_column(String, nullable=False, default="")
    provider: Mapped[str] = mapped_column(String, nullable=False, default="aws")
    region: Mapped[str] = mapped_column(String, nullable=False, default="")
    account_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    iac_status: Mapped[str] = mapped_column(String(20), nullable=False, default="undetermined")
    drift_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
