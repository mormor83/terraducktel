"""Pydantic schemas for ProxmoxCluster."""
import re
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services import account_colors
from app.services.proxmox_cluster_service import normalize_endpoint

# Operator-chosen natural key: 3–40 chars, lowercase, starts with a letter,
# ends alphanumeric. Encoded in repo paths as `proxmox/cluster-<slug>/…`.
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$"
# `user@realm!tokenid` — the `=` is excluded so a pasted "id=secret" pair is
# rejected instead of silently storing the secret in a plaintext column.
_TOKEN_ID_RE = re.compile(r"^[^\s!@=]+@[^\s!@=]+![^\s!=]+$")


def _https_endpoint(v: str) -> str:
    v = (v or "").strip()
    # Proxmox is always HTTPS on 8006; a bare `host:8006` pasted over the
    # form's `https://` prefill is the common case, so assume https rather
    # than bounce the operator. An explicit `http://` is still rejected.
    if v and "://" not in v:
        v = f"https://{v}"
    parts = urlsplit(v)
    if parts.scheme != "https":
        raise ValueError("endpoint must start with https://")
    if not parts.netloc:
        raise ValueError("endpoint must include a host")
    if parts.query:
        raise ValueError("endpoint must not include a query string")
    if parts.fragment:
        raise ValueError("endpoint must not include a fragment")
    path = parts.path.rstrip("/")
    if path not in ("", "/api2/json"):
        raise ValueError(
            "endpoint must not include a path other than a trailing /api2/json"
        )
    # Normalise here so the DB always holds a bare, lower-cased origin, e.g.
    # `https://host:8006` — not `HTTPS://Host:8006/api2/json/`.
    return normalize_endpoint(f"https://{parts.netloc.lower()}{parts.path}")


def _pem(v: Optional[str], header: str) -> Optional[str]:
    if v is None:
        return None
    if v.strip() == "":
        return ""  # "" = clear on update; create treats it as absent
    if not v.lstrip().startswith(f"-----BEGIN {header}"):
        raise ValueError(f"expected a PEM block starting with -----BEGIN {header}")
    return v


class ProxmoxClusterCreate(BaseModel):
    slug: str = Field(..., pattern=SLUG_PATTERN)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    endpoint: str = Field(..., min_length=12, max_length=255)
    api_token_id: str = Field(..., min_length=5, max_length=255)
    api_token_secret: str = Field(..., min_length=8)
    ssh_username: Optional[str] = Field(default=None, max_length=64)
    ssh_private_key: Optional[str] = None
    tls_insecure: bool = False
    ca_cert_pem: Optional[str] = None
    color: Optional[str] = None

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        return account_colors.normalize(v)

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v: str) -> str:
        return _https_endpoint(v)

    @field_validator("api_token_id")
    @classmethod
    def _token_id(cls, v: str) -> str:
        if not _TOKEN_ID_RE.match(v.strip()):
            raise ValueError("api_token_id must look like user@realm!tokenid")
        return v.strip()

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v):
        return _pem(v, "CERTIFICATE") or None

    @field_validator("ssh_private_key")
    @classmethod
    def _key(cls, v):
        # Accept OPENSSH / RSA / EC / generic PRIVATE KEY headers.
        if v is None or v.strip() == "":
            return None
        if "PRIVATE KEY-----" not in v.lstrip()[:64]:
            raise ValueError("ssh_private_key must be a PEM private key")
        return v

    @model_validator(mode="after")
    def _ssh_pair(self):
        if self.ssh_private_key and not (self.ssh_username or "").strip():
            raise ValueError("ssh_username is required when ssh_private_key is set")
        return self


class ProxmoxClusterUpdate(BaseModel):
    """All optional. Secret fields: omitted = unchanged, "" = clear (SSH only)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    endpoint: Optional[str] = Field(default=None, max_length=255)
    api_token_id: Optional[str] = Field(default=None, max_length=255)
    api_token_secret: Optional[str] = Field(default=None, min_length=8)
    ssh_username: Optional[str] = Field(default=None, max_length=64)
    ssh_private_key: Optional[str] = None
    tls_insecure: Optional[bool] = None
    ca_cert_pem: Optional[str] = None
    color: Optional[str] = None

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        return account_colors.normalize(v)

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v):
        return _https_endpoint(v) if v is not None else v

    @field_validator("api_token_id")
    @classmethod
    def _token_id(cls, v):
        if v is None:
            return v
        if not _TOKEN_ID_RE.match(v.strip()):
            raise ValueError("api_token_id must look like user@realm!tokenid")
        return v.strip()

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v):
        return _pem(v, "CERTIFICATE")

    @field_validator("ssh_private_key")
    @classmethod
    def _key(cls, v):
        if v is None or v.strip() == "":
            return v
        if "PRIVATE KEY-----" not in v.lstrip()[:64]:
            raise ValueError("ssh_private_key must be a PEM private key")
        return v


class ProxmoxClusterResponse(BaseModel):
    """NEVER carries the token secret or the SSH private key."""
    id: str
    business_unit_id: str
    slug: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    color_effective: str = "gray"
    endpoint: str
    api_token_id: str
    token_secret_masked_tail: str
    ssh_username: Optional[str] = None
    has_ssh_key: bool = False
    tls_insecure: bool = False
    ca_cert_pem: Optional[str] = None

    model_config = {"from_attributes": False}


class ProxmoxClusterTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    version: Optional[str] = None
