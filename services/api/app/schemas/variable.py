"""Pydantic schemas for global + workspace + per-run variables.

Three scopes, identical shape so the UI can render them with one component.
Secrets are write-once: subsequent reads return `value=None` and the masked
tail in `masked_tail`. Plaintext NEVER leaves the API after creation.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Terraform input variables must match `^[a-zA-Z_][a-zA-Z0-9_]*$` (the same
# rule as HCL identifiers). Enforced at the schema layer so a 422 fires before
# we ever encrypt + write something the executor would reject.
_TF_VAR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class VariableBase(BaseModel):
    key: str = Field(..., min_length=1, max_length=255)
    is_secret: bool = False
    is_hcl: bool = False
    description: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not _TF_VAR_KEY_RE.match(v):
            raise ValueError(
                "key must match ^[A-Za-z_][A-Za-z0-9_]*$ (terraform identifier rules)"
            )
        return v


class VariableCreate(VariableBase):
    value: str = Field(..., min_length=0)


class VariableUpdate(BaseModel):
    """All fields optional. `value` re-encrypts; omitting it leaves stored
    ciphertext untouched (rotate metadata without re-supplying the secret)."""

    # `key` is intentionally immutable post-create to keep audit and merge
    # semantics simple — to "rename" a var, delete + recreate.
    is_secret: Optional[bool] = None
    is_hcl: Optional[bool] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    value: Optional[str] = None


class VariableResponse(BaseModel):
    """Response shape — NEVER returns plaintext for `is_secret=True` rows.

    For non-secret rows, `value` is populated so the UI can show the actual
    string without a round-trip. For secret rows, `value=None` and the caller
    sees `masked_tail` (e.g., `…ab12`) — enough to identify which value is
    configured without leaking it.
    """

    id: str
    scope: Literal["global", "workspace", "run"]
    workspace_id: Optional[str] = None
    key: str
    is_secret: bool
    is_hcl: bool
    description: Optional[str] = None
    value: Optional[str] = None
    masked_tail: Optional[str] = None


class RunVariable(BaseModel):
    """Per-run variable supplied in the trigger-run POST body.

    Lives only on the run row (encrypted as a single JSON blob, not as
    individual rows in a separate table — they're never queried independently).
    """

    key: str = Field(..., min_length=1, max_length=255)
    value: str = Field(..., min_length=0)
    is_secret: bool = False
    is_hcl: bool = False

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not _TF_VAR_KEY_RE.match(v):
            raise ValueError(
                "key must match ^[A-Za-z_][A-Za-z0-9_]*$ (terraform identifier rules)"
            )
        return v
