"""Pydantic schemas for environment links (Governance › Environments)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Level = Literal["account", "region", "folder", "stack"]


class EnvNode(BaseModel):
    level: Level
    path: str = Field(min_length=1, max_length=500)


class RewriteRule(BaseModel):
    from_: str = Field(alias="from", min_length=1, max_length=200)
    to: str = Field(default="", max_length=200)
    regex: bool = False

    model_config = ConfigDict(populate_by_name=True)

    def as_dict(self) -> dict:
        return {"from": self.from_, "to": self.to, "regex": self.regex}


class PairOverride(BaseModel):
    source: str = Field(max_length=500)
    target: str = Field(max_length=500)


class PairExclusion(BaseModel):
    side: Literal["source", "target"]
    path: str = Field(default="", max_length=500)


class PairOverrides(BaseModel):
    pairs: list[PairOverride] = Field(default_factory=list, max_length=500)
    exclude: list[PairExclusion] = Field(default_factory=list, max_length=500)


class ProtectedRules(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=200)
    values: list[str] = Field(default_factory=list, max_length=200)


class EnvLinkRules(BaseModel):
    """The pairing-relevant part of a link — also the preview-pairs body."""

    source_node: EnvNode
    target_node: EnvNode
    rewrite_rules: list[RewriteRule] = Field(default_factory=list, max_length=20)
    pair_overrides: PairOverrides = Field(default_factory=PairOverrides)


class EnvLinkCreate(EnvLinkRules):
    name: str = Field(min_length=1, max_length=120)
    # Omitted → seeded with the defaults (env-specific keys + both account ids).
    protected_rules: Optional[ProtectedRules] = None


class EnvLinkUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    source_node: Optional[EnvNode] = None
    target_node: Optional[EnvNode] = None
    rewrite_rules: Optional[list[RewriteRule]] = Field(default=None, max_length=20)
    pair_overrides: Optional[PairOverrides] = None
    protected_rules: Optional[ProtectedRules] = None


class PairOut(BaseModel):
    id: Optional[str] = None  # None in a preview (nothing persisted yet)
    relative_path: str
    source_rel: Optional[str] = None
    target_rel: Optional[str] = None
    source_stack_id: Optional[str] = None
    target_stack_id: Optional[str] = None
    source_stack_name: Optional[str] = None
    target_stack_name: Optional[str] = None
    status: str
    reason: Optional[str] = None
    proposed_target_rel: Optional[str] = None
    summary: Optional[dict] = None
    last_compared_at: Optional[datetime] = None


class PairSummary(BaseModel):
    not_compared: int = 0
    in_sync: int = 0
    diverged: int = 0
    missing_in_target: int = 0
    missing_in_source: int = 0
    excluded: int = 0
    total: int = 0


class PairingPreview(BaseModel):
    level: Level
    source_account_id: Optional[str] = None
    target_account_id: Optional[str] = None
    pairs: list[PairOut]
    summary: PairSummary
    helm_skipped: int = 0
    warnings: list[str] = Field(default_factory=list)
    # The protected rules a new link would be seeded with for these nodes.
    default_protected_rules: ProtectedRules


class EnvLinkResponse(BaseModel):
    id: str
    business_unit_id: str
    name: str
    engine: str
    source_node: EnvNode
    target_node: EnvNode
    level: Level
    source_account_id: Optional[str] = None
    target_account_id: Optional[str] = None
    rewrite_rules: list[dict]
    pair_overrides: dict
    protected_rules: dict
    rules_version: int
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    pair_summary: PairSummary
    helm_skipped: int = 0
    warnings: list[str] = Field(default_factory=list)
    # Filled once promotions exist (Phase 2); always null for now.
    last_promotion: Optional[dict] = None


class PairListResponse(BaseModel):
    items: list[PairOut]
    summary: PairSummary
    warnings: list[str] = Field(default_factory=list)
