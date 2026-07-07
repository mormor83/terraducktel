"""Policy — BU-scoped OPA/conftest rego rule + its append-only version history.

A `Policy` is a single rego document (authored in the TDT UI) that the executor
runs against a Terraform plan via `conftest`, and that the API can dry-run /
unit-test synchronously. Tenancy is per Business Unit, like every other tenant
row. Each policy carries its own `severity` (`block | warn | info`); the per-BU
`opa.mode` (`enforce | warn | off`, in the Config table) is the master switch
that decides whether a `block` violation actually fails a run.

Every create and every edit snapshots the full policy content into a
`PolicyVersion` row (monotonic `version` per policy). "Restore" copies an old
snapshot's content into a NEW current version — history is append-only, never
rewritten. The tamper-evident `audit_log` records who/when on top.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# block: a violation fails the run under enforce mode.
# warn:  advisory under enforce; recorded but never blocks.
# info:  informational only.
SEVERITIES = ("block", "warn", "info")


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "name", name="uq_policies_bu_name"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    business_unit_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("business_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The rego source — `package main` with `deny[msg]` / `warn[msg]` rules,
    # matching the bundled defaults in policies/*.rego (conftest convention).
    rego: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional `test_*` rego, run by `conftest verify` from the editor.
    tests_rego: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="block", default="block"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    # Latest version number; bumped on every edit/restore. Starts at 1.
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PolicyVersion(Base):
    """Immutable snapshot of a Policy's content at a point in time.

    One row per create/edit/restore. The set of (policy_id, version) is unique
    and `version` is monotonic per policy. Restoring an old version inserts a
    brand-new row with the next version number, never mutating history.
    """

    __tablename__ = "policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_id", "version", name="uq_policy_versions_policy_version"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    policy_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot of the policy fields at this revision.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rego: Mapped[str] = mapped_column(Text, nullable=False)
    tests_rego: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
