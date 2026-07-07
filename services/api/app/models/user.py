import uuid
from sqlalchemy import Boolean, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy global role. Kept for one release as a fallback for code paths
    # that haven't migrated to per-BU role resolution. New checks should
    # consult `is_superadmin` and `user_business_units.role` instead.
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")  # admin, operator, viewer
    # Cross-BU superadmin. True for the old `admin` users; bypasses BU scoping
    # entirely (sees all BUs, all workspaces, all accounts).
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="local")  # local, oidc
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Human-readable name from the OIDC `name` claim (falls back to
    # given+family or preferred_username inside upsert_oidc_user). NULL for
    # local users — the UI prettifies the email local part in that case.
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
