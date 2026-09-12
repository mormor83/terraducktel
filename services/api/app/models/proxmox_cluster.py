"""ProxmoxCluster model — one row per Proxmox VE cluster (or standalone node)
onboarded to TDT.

Mirrors GcpProject / AzureSubscription / AwsAccount: same encryption scheme
(a distinct HKDF salt so a Proxmox token can't be confused with another
provider's secret), same per-BU uniqueness story, same "credentials never
leave the API" rule.

Proxmox has no globally unique cluster identifier (a standalone node has no
cluster name at all), so `slug` is the operator-chosen natural key. It is
what the repo path convention `proxmox/cluster-<slug>/<node>/<stack>` encodes.

Both Terraform providers (`bpg/proxmox`, `Telmate/proxmox`) are driven from
this one credential: the executor exports each provider's env-var vocabulary
from the same endpoint + API token. No object store exists on Proxmox, so
workspaces linked here always keep `state_backend=s3`.
"""
import uuid

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProxmoxCluster(Base):
    __tablename__ = "proxmox_clusters"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "slug", name="uq_proxmox_clusters_bu_slug"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # Operator-chosen natural key within a BU; appears in repo paths.
    slug: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional UI colour token (see app.services.account_colors). NULL = auto.
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # `https://host:8006` — scheme+host+port only; normalised on write.
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    # `user@realm!tokenid`. An identifier, not a secret — shown in the UI.
    api_token_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted token secret. NEVER logged, NEVER returned in responses.
    api_token_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional SSH identity for bpg resources that upload files to a node.
    ssh_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Skip TLS verification (self-signed homelab certs). Default off.
    tls_insecure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional PEM CA bundle; public material, stored plain.
    ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
