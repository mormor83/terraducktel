"""Pydantic schemas for users."""
from typing import Optional
from pydantic import BaseModel, Field


class UserMembership(BaseModel):
    business_unit_id: str
    business_unit_slug: str
    business_unit_name: str
    role: str  # operator | viewer


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    auth_provider: str
    external_id: Optional[str] = None
    is_superadmin: bool = False
    memberships: list[UserMembership] = []

    model_config = {"from_attributes": True}


class MembershipUpsert(BaseModel):
    business_unit_id: str
    role: str = Field(..., pattern=r"^(operator|viewer)$")


class UserPatch(BaseModel):
    """Superadmin-only edits.

    `is_superadmin`: promote/demote.
    `add_memberships` / `remove_memberships`: granular BU access. Memberships
    do not apply to superadmins (they bypass the table).
    """
    is_superadmin: Optional[bool] = None
    add_memberships: Optional[list[MembershipUpsert]] = None
    remove_memberships: Optional[list[str]] = None  # list of business_unit_id
