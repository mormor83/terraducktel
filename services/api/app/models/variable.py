"""Variable models — global and per-workspace TF_VAR_* sources.

Merge precedence at executor launch: `global ← workspace ← run`; last wins per
key. Run-scope values live as a Fernet JSON blob on `Run.variables_encrypted`
(see `app.models.run`), not here — keeps the join story for plan/apply replay
simple (one row, one blob, decrypted once).

`value_encrypted` ciphertext uses the same Fernet scheme as `AwsAccount` and
`ConfigService` (HKDF over CREDENTIAL_ENCRYPTION_KEY). NEVER logged, NEVER
returned via the API in plaintext after first save — read paths use the
`{configured, masked_tail}` shape from `aws_account_service`.
"""
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GlobalVariable(Base):
    __tablename__ = "global_variables"
    # Per-BU scope: the same key may exist in different BUs, but is unique
    # within one BU. (Previously globally unique → the var leaked across BUs.)
    __table_args__ = (
        UniqueConstraint("business_unit_id", "key", name="uq_global_variables_bu_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Owning Business Unit — "global within a BU", not org-wide.
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When true, value is parsed as an HCL expression (list/map/etc.) by
    # terraform via `TF_VAR_<key>='[…]'`. Otherwise treated as a plain string.
    is_hcl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceVariable(Base):
    __tablename__ = "workspace_variables"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_workspace_variables_workspace_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_hcl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
