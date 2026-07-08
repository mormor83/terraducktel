"""Pydantic schemas for GcpProject."""
import json
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# GCP project ids: 6-30 chars, start with a lowercase letter, then lowercase
# letters / digits / hyphens, no trailing hyphen.
_PROJECT_ID_PATTERN = r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"


def _validate_sa_json(v: str) -> str:
    """Structurally validate an uploaded service-account key JSON."""
    try:
        data = json.loads(v)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("service_account_json must be valid JSON")
    if not isinstance(data, dict):
        raise ValueError("service_account_json must be a JSON object")
    if data.get("type") != "service_account":
        raise ValueError('service_account_json must have "type": "service_account"')
    for field in ("client_email", "private_key", "project_id"):
        if not data.get(field):
            raise ValueError(f"service_account_json missing required field: {field}")
    return v


class GcpProjectCreate(BaseModel):
    project_id: str = Field(..., pattern=_PROJECT_ID_PATTERN)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    default_region: str = Field(default="us-central1", min_length=1, max_length=50)
    # Optional GCS state bucket (for workspaces flagged state_backend=gcs).
    state_bucket: Optional[str] = Field(default=None, max_length=255)
    state_prefix: Optional[str] = Field(default=None, max_length=255)
    # The full service-account key JSON (pasted from the downloaded key file).
    # Validated structurally here; encrypted at rest by the service layer.
    service_account_json: str = Field(..., min_length=50)

    @field_validator("service_account_json")
    @classmethod
    def _sa(cls, v: str) -> str:
        return _validate_sa_json(v)


class GcpProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    default_region: Optional[str] = None
    state_bucket: Optional[str] = None
    state_prefix: Optional[str] = None
    # If provided, re-encrypted on the server.
    service_account_json: Optional[str] = None

    @field_validator("service_account_json")
    @classmethod
    def _sa(cls, v: Optional[str]) -> Optional[str]:
        return _validate_sa_json(v) if v is not None else v


class GcpProjectResponse(BaseModel):
    """Response shape — NEVER returns the plaintext SA key JSON.

    `service_account_masked` is the SA client_email (already low-sensitivity,
    it's an identifier) so admins can tell which SA is configured.
    """
    id: str
    business_unit_id: str
    project_id: str
    client_email: str
    name: str
    description: Optional[str] = None
    default_region: str
    state_bucket: Optional[str] = None
    state_prefix: Optional[str] = None
    service_account_masked: str

    model_config = {"from_attributes": False}


class GcpProjectTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    client_email: Optional[str] = None


class GcpBucketResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    bucket: Optional[str] = None
    already_existed: bool = False
