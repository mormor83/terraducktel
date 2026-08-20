"""Pydantic schemas for AwsAccount."""
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.services import account_colors


class AwsAccountCreate(BaseModel):
    account_id: str = Field(..., pattern=r"^\d{12}$")
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    state_bucket: str = Field(..., min_length=3, max_length=255)
    state_bucket_region: str = "us-east-1"
    default_region: str = "us-east-1"
    aws_profile_name: Optional[str] = None
    # Cosmetic UI/Slack colour token; None/"" = auto-derive. See account_colors.
    color: Optional[str] = None
    access_key_id: str = Field(..., min_length=4)
    secret_access_key: str = Field(..., min_length=4)

    @field_validator("color")
    @classmethod
    def _color(cls, v: Optional[str]) -> Optional[str]:
        return account_colors.normalize(v)

    @field_validator("state_bucket")
    @classmethod
    def _bucket_chars(cls, v: str) -> str:
        # AWS S3 bucket name rules: lowercase letters, digits, hyphens, dots
        import re
        if not re.match(r"^[a-z0-9][a-z0-9.\-]{1,253}[a-z0-9]$", v):
            raise ValueError("state_bucket must be a valid S3 bucket name (lowercase, 3-255 chars)")
        return v


class AwsAccountUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    state_bucket: Optional[str] = None
    state_bucket_region: Optional[str] = None
    default_region: Optional[str] = None
    aws_profile_name: Optional[str] = None
    # Send "" (or null) to clear back to the auto-derived colour.
    color: Optional[str] = None
    # If either credential is provided we re-encrypt on the server.
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None

    @field_validator("color")
    @classmethod
    def _color(cls, v: Optional[str]) -> Optional[str]:
        return account_colors.normalize(v)


class AwsAccountResponse(BaseModel):
    """Response shape — NEVER returns plaintext credentials.

    `access_key_id_masked` is just the AKIA…XXXX tail so admins can identify
    which key is configured without leaking it.
    """
    id: str
    business_unit_id: str
    account_id: str
    name: str
    description: Optional[str] = None
    state_bucket: str
    state_bucket_region: str
    default_region: str
    aws_profile_name: Optional[str] = None
    # `color` is what's stored (None = auto, so Settings can show "Auto"
    # selected); `color_effective` is what the UI actually paints and is
    # always populated. Two fields so the derived-default hash lives in
    # exactly one place — the server.
    color: Optional[str] = None
    color_effective: str = "gray"
    access_key_id_masked: str

    model_config = {"from_attributes": False}


class AwsAccountTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    bucket_exists: Optional[bool] = None
    caller_arn: Optional[str] = None


class CreateBucketResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    bucket: str
    already_existed: bool = False
