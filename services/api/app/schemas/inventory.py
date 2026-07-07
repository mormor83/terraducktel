"""Firefly-style cloud asset inventory schemas."""
from pydantic import BaseModel


class AssetIn(BaseModel):
    """One classified asset, posted by the detector inside a drift report."""

    asset_id: str
    address: str = ""
    asset_type: str = ""
    provider: str = "aws"
    region: str = ""
    account_id: str = ""
    # one of: codified | drifted | ghost | unmanaged | ignored | undetermined
    iac_status: str = "undetermined"
    drift_summary: str = ""


class AssetOut(BaseModel):
    asset_id: str
    address: str = ""
    asset_type: str = ""
    provider: str = ""
    region: str = ""
    account_id: str = ""
    iac_status: str
    drift_summary: str = ""
    workspace_id: str | None = None
    last_seen: str | None = None


class InventoryFacets(BaseModel):
    """Distinct filter values present in the current BU's inventory."""

    providers: list[str] = []
    regions: list[str] = []
    accounts: list[str] = []
    asset_types: list[str] = []


class InventorySummaryOut(BaseModel):
    """Headline KPIs for the Inventory dashboard."""

    total: int = 0
    codification_pct: int = 0  # tracked-by-IaC / total, 0..100
    counts: dict[str, int] = {}  # iac_status -> count, all 6 states present
    facets: InventoryFacets = InventoryFacets()


class AssetListOut(BaseModel):
    total: int = 0
    items: list[AssetOut] = []


class IgnoreRuleIn(BaseModel):
    match_type: str  # arn_glob | asset_type
    pattern: str
    note: str = ""


class IgnoreRuleOut(BaseModel):
    id: str
    match_type: str
    pattern: str
    note: str = ""
    created_at: str | None = None
