"""Drift detection API schemas."""
from pydantic import BaseModel

from app.schemas.inventory import AssetIn


class DriftResource(BaseModel):
    """One drifted resource, classified into a single drift category."""

    address: str
    type: str = ""
    provider: str = ""
    # one of: "modified" | "untracked" | "deleted" | "mismatch"
    drift_type: str
    summary: str = ""


class DriftReportIn(BaseModel):
    workspace_id: str
    has_drift: bool
    summary: str = ""
    plan_output: str = ""
    # Per-category breakdown. Optional so older detectors posting the minimal
    # shape still work (counts default to 0, resources to []).
    modified_count: int = 0
    untracked_count: int = 0
    deleted_count: int = 0
    mismatch_count: int = 0
    resources: list[DriftResource] = []
    # Full classified asset list for the Firefly-style inventory. Optional so
    # older detectors posting only drift counts still work.
    assets: list[AssetIn] = []


class DriftReportOut(BaseModel):
    report_id: str
    workspace_id: str
    has_drift: bool


class DriftWorkspaceSummary(BaseModel):
    """Per-workspace row in the BU drift summary."""

    workspace_id: str
    name: str
    environment: str
    region: str
    drift_status: str
    modified_count: int = 0
    untracked_count: int = 0
    deleted_count: int = 0
    mismatch_count: int = 0


class DriftSummaryOut(BaseModel):
    """Per-BU aggregate of the latest drift report for each workspace."""

    modified_count: int = 0
    untracked_count: int = 0
    deleted_count: int = 0
    mismatch_count: int = 0
    workspaces_total: int = 0
    workspaces_drifted: int = 0
    by_workspace: list[DriftWorkspaceSummary] = []


class DriftReportDetailOut(BaseModel):
    """Latest drift report for one workspace, including per-resource detail."""

    workspace_id: str
    has_drift: bool
    summary: str = ""
    detected_at: str | None = None
    modified_count: int = 0
    untracked_count: int = 0
    deleted_count: int = 0
    mismatch_count: int = 0
    resources: list[DriftResource] = []
