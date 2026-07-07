"""Per-BU inventory ignore rules.

Lets operators suppress inventory noise — a live resource matching a rule is
reclassified to `ignored` at ingest (and excluded from the codification base).
Two match types evaluable from stored CloudAsset fields:

  arn_glob   — fnmatch against the asset's ARN/id (e.g. `arn:aws:cloudformation:*:*:stack/StackSet-*`)
  asset_type — exact match on the asset type (e.g. `aws_cloudwatch_log_group`)

Service-owned children (EKS/CFN/Karpenter/…) are auto-classified as
`service_managed` by the collector via tags; ignore rules cover whatever that
misses.
"""
import uuid

from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

IGNORE_MATCH_TYPES = ("arn_glob", "asset_type")


class InventoryIgnoreRule(Base):
    __tablename__ = "inventory_ignore_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    business_unit_id: Mapped[str] = mapped_column(
        String, ForeignKey("business_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_type: Mapped[str] = mapped_column(String(20), nullable=False)  # arn_glob | asset_type
    pattern: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)  # user id
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
