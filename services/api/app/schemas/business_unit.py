"""Pydantic schemas for BusinessUnit."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


class BusinessUnitCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters/digits/hyphens, 3-64 chars, "
                "no leading/trailing hyphen"
            )
        return v


class BusinessUnitUpdate(BaseModel):
    # Slug is immutable post-create — name is the only editable field.
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class BusinessUnitResponse(BaseModel):
    id: str
    slug: str
    name: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
