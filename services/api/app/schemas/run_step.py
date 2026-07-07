"""Pydantic schemas for RunStep."""
from typing import Literal, Optional

from pydantic import BaseModel


StepStatusLiteral = Literal["pending", "running", "success", "failed", "skipped"]


class RunStepUpdate(BaseModel):
    """Executor-driven update for a single step."""
    status: Optional[StepStatusLiteral] = None
    output: Optional[str] = None
    summary_json: Optional[str] = None


class RunStepResponse(BaseModel):
    id: str
    run_id: str
    position: int
    name: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    output: Optional[str] = None
    summary_json: Optional[str] = None

    model_config = {"from_attributes": True}
