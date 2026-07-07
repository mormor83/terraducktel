import uuid
from sqlalchemy import String, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """Append-only audit trail.

    `prev_hash` + `entry_hash` form a chain over canonicalised row content.
    A DB trigger (see migration 015) enforces:
      - entry_hash = sha256(prev_hash || <canonical-row-json>)
      - prev_hash on insert must equal the most-recent entry_hash in the table
        (or the empty string for the very first row).
      - UPDATE and DELETE are rejected at the row level.

    The Python side computes the hash before INSERT so the trigger has
    something to compare against. The verifier endpoint walks the chain and
    flags any row whose stored hash doesn't reproduce.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 64-char lowercase hex SHA-256 digests. Computed by app code; verified by
    # the DB trigger. The very first row has prev_hash="" (empty string, not NULL).
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
