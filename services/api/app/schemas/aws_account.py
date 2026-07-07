"""Pydantic schemas for AwsAccount."""
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AwsAccountCreate(BaseModel):
    account_id: str = Field(..., pattern=r"^\d{12}$")
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    state_bucket: str = Field(..., min_length=3, max_length=255)
    state_bucket_region: str = "us-east-1"
    default_region: str = "us-east-1"
    aws_profile_name: Optional[str] = None
    access_key_id: str = Field(..., min_length=4)
    secret_access_key: str = Field(..., min_length=4)

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
    # If either credential is provided we re-encrypt on the server.
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None


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
