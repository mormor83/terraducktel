"""Pydantic schemas for AzureSubscription."""
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Azure tenant/subscription/client/object ids are UUIDs. Accept the canonical
# 8-4-4-4-12 hex form, case-insensitive.
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class AzureSubscriptionCreate(BaseModel):
    subscription_id: str = Field(..., pattern=_UUID_PATTERN)
    tenant_id: str = Field(..., pattern=_UUID_PATTERN)
    client_id: str = Field(..., pattern=_UUID_PATTERN)
    client_secret: str = Field(..., min_length=4)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    default_location: str = Field(default="eastus", min_length=1, max_length=50)

    @field_validator("subscription_id", "tenant_id", "client_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class AzureSubscriptionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    default_location: Optional[str] = None
    # If client_secret is provided we re-encrypt it on the server.
    client_secret: Optional[str] = None


class AzureSubscriptionResponse(BaseModel):
    """Response shape — NEVER returns the plaintext SP secret.

    `client_secret_masked` is just the last 4 chars of the original secret
    so admins can identify which SP is configured without leaking it.
    """
    id: str
    business_unit_id: str
    subscription_id: str
    tenant_id: str
    client_id: str
    name: str
    description: Optional[str] = None
    default_location: str
    client_secret_masked: str

    model_config = {"from_attributes": False}


class AzureSubscriptionTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
