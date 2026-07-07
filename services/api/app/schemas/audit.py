"""Audit log API schemas."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    user_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: str
    workspace_id: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    items: list[AuditLogEntry]
