"""Environment links — a saved source-node → target-node relation + its pairs.

An `EnvLink` joins two nodes of the synced repo tree (account / region /
folder / stack, both at the same level, same BU, terraform only) so the
Governance › Environments screen can pair their stacks, show what differs and
promote selected differences source → target through the normal gated
pipeline. The link carries the rules that drive pairing (`rewrite_rules`,
`pair_overrides`) and classification (`protected_rules`); `rules_version`
bumps on every change to them so cached compare results keyed on it go stale.

`EnvPair` rows are the materialised output of pairing (see
`app/services/env_pairing.py`), reconciled whenever the link or the BU's
workspaces change. A pair's `pair_key` (`<source rel>=><target rel>`) is its
stable identity, so its id — and later its compare results — survive
re-pairing as long as the same two paths still match.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EnvLink(Base):
    __tablename__ = "env_links"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "name", name="uq_env_links_bu_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    business_unit_id: Mapped[str] = mapped_column(
        String, ForeignKey("business_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Diff engine for this link. Only "terraform" in v1; "helm" is reserved.
    engine: Mapped[str] = mapped_column(String(20), nullable=False, default="terraform")
    # {"level": "account|region|folder|stack", "path": "<repo-relative path>"}
    source_node: Mapped[dict] = mapped_column(JSON, nullable=False)
    target_node: Mapped[dict] = mapped_column(JSON, nullable=False)
    # [{"from": str, "to": str, "regex": bool}] — applied in order to source paths.
    rewrite_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # {"pairs": [{"source": rel, "target": rel}], "exclude": [{"side", "path"}]}
    pair_overrides: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {"keys": [glob…], "values": [glob…]} — env-specific values kept out of promotion.
    protected_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rules_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EnvPair(Base):
    __tablename__ = "env_pairs"
    __table_args__ = (
        UniqueConstraint("link_id", "pair_key", name="uq_env_pairs_link_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    link_id: Mapped[str] = mapped_column(
        String, ForeignKey("env_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pair_key: Mapped[str] = mapped_column(String(1100), nullable=False)
    # Display path (target side when present), plus each side's raw relative path.
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_rel: Mapped[str | None] = mapped_column(String(500), nullable=True)
    target_rel: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_stack_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_stack_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # not_compared | in_sync | diverged | missing_in_target | missing_in_source | excluded
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_compared")
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # missing_in_target only: the target-relative path a create would use.
    proposed_target_rel: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cache_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_compared_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnvCompareResult(Base):
    """Latest compare of one pair in one direction.

    `cache_key` = sha256 of (source commit, target commit, source state serial,
    target state serial, link rules_version, direction). A read whose freshly
    computed key matches can serve this row; anything else recomputes.
    """

    __tablename__ = "env_compare_results"
    __table_args__ = (
        UniqueConstraint("pair_id", "direction", name="uq_env_compare_pair_dir"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pair_id: Mapped[str] = mapped_column(
        String, ForeignKey("env_pairs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="forward")
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # {"source": {"commit", "branch", "repo_url"}, "target": {...}} at compute time.
    refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
