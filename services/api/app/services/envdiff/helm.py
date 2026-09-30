"""Helm diff engine — stub. v1 environment promotion is terraform-only.

A real implementation would diff chart version + values files through the
same :class:`~app.services.envdiff.base.DiffEngine` interface.
"""
from __future__ import annotations

from app.services.envdiff.base import ConfigDiff

_MSG = "Helm environments are coming soon"


class HelmEngine:
    name = "helm"

    def config_diff(self, source_files, target_files, protected_rules) -> ConfigDiff:
        raise NotImplementedError(_MSG)

    def apply_hunks(self, source_files, target_files, hunk_ids, protected_rules):
        raise NotImplementedError(_MSG)
