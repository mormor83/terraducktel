"""Policy service — CRUD + version history + the conftest engine.

Two responsibilities:

1. **Persistence** — BU-scoped CRUD over `policies`, every write snapshotting
   into `policy_versions` and recording a tamper-evident `audit_log` row. Restore
   copies an old snapshot into a NEW current version (append-only history).

2. **Evaluation** — `evaluate()` (dry-run rego against a plan) and `verify()`
   (run rego unit tests) shell out to the same `conftest` binary the executor
   uses, so the in-app authoring loop matches real-run results exactly. Rego is
   sandboxed (no fs/net), so evaluating user-authored rego in-process is safe;
   we still hard-timeout and never raise engine errors to the caller.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.policy import SEVERITIES, Policy, PolicyVersion
from app.schemas.policy import (
    PolicyCreate,
    PolicyTestResult,
    PolicyUpdate,
    PolicyVerifyResult,
    Violation,
)
from app.services.audit_chain import stamp

logger = logging.getLogger(__name__)

# Overridable for tests / non-PATH installs.
CONFTEST_BIN = os.environ.get("CONFTEST_BIN", "conftest")
_EVAL_TIMEOUT = 20  # seconds, per conftest invocation
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


# ─── persistence ─────────────────────────────────────────────────────────────


async def list_policies(db: AsyncSession, bu_id: str) -> list[Policy]:
    rows = (
        await db.execute(
            select(Policy)
            .where(Policy.business_unit_id == bu_id)
            .order_by(Policy.name)
        )
    ).scalars().all()
    return list(rows)


async def get_policy(db: AsyncSession, bu_id: str, policy_id: str) -> Policy:
    policy = await db.get(Policy, policy_id)
    if policy is None or policy.business_unit_id != bu_id:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


async def list_enabled(db: AsyncSession, bu_id: str) -> list[Policy]:
    rows = (
        await db.execute(
            select(Policy)
            .where(Policy.business_unit_id == bu_id, Policy.enabled.is_(True))
            .order_by(Policy.name)
        )
    ).scalars().all()
    return list(rows)


def _snapshot(db: AsyncSession, policy: Policy, changed_by: Optional[str]) -> None:
    """Append an immutable PolicyVersion row at the policy's current_version."""
    db.add(
        PolicyVersion(
            policy_id=policy.id,
            version=policy.current_version,
            name=policy.name,
            description=policy.description,
            rego=policy.rego,
            tests_rego=policy.tests_rego,
            severity=policy.severity,
            enabled=policy.enabled,
            changed_by=changed_by,
        )
    )


async def _audit(
    db: AsyncSession, *, user_id: Optional[str], action: str, policy: Policy
) -> None:
    audit = AuditLog(
        user_id=user_id,
        action=action,
        resource_type="policy",
        resource_id=policy.id,
        details={
            "name": policy.name,
            "severity": policy.severity,
            "enabled": policy.enabled,
            "version": policy.current_version,
            "business_unit_id": policy.business_unit_id,
        },
    )
    db.add(audit)
    await stamp(db, audit)


async def _assert_unique_name(
    db: AsyncSession, bu_id: str, name: str, *, exclude_id: Optional[str] = None
) -> None:
    stmt = select(Policy.id).where(
        Policy.business_unit_id == bu_id, Policy.name == name
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None and existing != exclude_id:
        raise HTTPException(
            status_code=409, detail=f"A policy named '{name}' already exists in this BU"
        )


async def create_policy(
    db: AsyncSession, bu_id: str, body: PolicyCreate, user_id: Optional[str]
) -> Policy:
    await _assert_unique_name(db, bu_id, body.name)
    policy = Policy(
        business_unit_id=bu_id,
        name=body.name,
        description=body.description,
        rego=body.rego,
        tests_rego=body.tests_rego,
        severity=body.severity,
        enabled=body.enabled,
        current_version=1,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(policy)
    await db.flush()
    _snapshot(db, policy, user_id)
    await _audit(db, user_id=user_id, action="policy.create", policy=policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def update_policy(
    db: AsyncSession,
    bu_id: str,
    policy_id: str,
    body: PolicyUpdate,
    user_id: Optional[str],
) -> Policy:
    policy = await get_policy(db, bu_id, policy_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != policy.name:
        await _assert_unique_name(db, bu_id, data["name"], exclude_id=policy.id)
    for field, value in data.items():
        setattr(policy, field, value)
    policy.current_version += 1
    policy.updated_by = user_id
    await db.flush()
    _snapshot(db, policy, user_id)
    await _audit(db, user_id=user_id, action="policy.update", policy=policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def delete_policy(
    db: AsyncSession, bu_id: str, policy_id: str, user_id: Optional[str]
) -> None:
    policy = await get_policy(db, bu_id, policy_id)
    await _audit(db, user_id=user_id, action="policy.delete", policy=policy)
    await db.delete(policy)
    await db.commit()


async def list_versions(
    db: AsyncSession, bu_id: str, policy_id: str
) -> list[PolicyVersion]:
    await get_policy(db, bu_id, policy_id)  # BU-scope guard
    rows = (
        await db.execute(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == policy_id)
            .order_by(PolicyVersion.version.desc())
        )
    ).scalars().all()
    return list(rows)


async def restore_version(
    db: AsyncSession, bu_id: str, policy_id: str, version: int, user_id: Optional[str]
) -> Policy:
    policy = await get_policy(db, bu_id, policy_id)
    snap = (
        await db.execute(
            select(PolicyVersion).where(
                PolicyVersion.policy_id == policy_id,
                PolicyVersion.version == version,
            )
        )
    ).scalars().first()
    if snap is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    # Copy the snapshot's content into a brand-new current version.
    policy.name = snap.name
    policy.description = snap.description
    policy.rego = snap.rego
    policy.tests_rego = snap.tests_rego
    policy.severity = snap.severity
    policy.enabled = snap.enabled
    policy.current_version += 1
    policy.updated_by = user_id
    await db.flush()
    _snapshot(db, policy, user_id)
    await _audit(db, user_id=user_id, action="policy.restore", policy=policy)
    await db.commit()
    await db.refresh(policy)
    return policy


# ─── conftest engine ─────────────────────────────────────────────────────────


def _safe_dirname(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", name).strip("_")
    return cleaned or "policy"


async def _run(*args: str, timeout: int = _EVAL_TIMEOUT) -> tuple[int, str, str]:
    """Run conftest; return (returncode, stdout, stderr). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            CONFTEST_BIN,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"{CONFTEST_BIN} not found on PATH"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"conftest timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _resource_of(finding: dict) -> Optional[str]:
    """Best-effort resource address from a conftest finding's metadata."""
    meta = finding.get("metadata") or {}
    for key in ("resource", "address", "name"):
        val = meta.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _parse_conftest(stdout: str) -> tuple[list[dict], list[dict], Optional[str]]:
    """Return (failures, warnings, parse_error) from conftest JSON output."""
    try:
        results = json.loads(stdout) if stdout.strip() else []
    except json.JSONDecodeError as exc:
        return [], [], f"could not parse conftest output: {exc}"
    failures: list[dict] = []
    warnings: list[dict] = []
    for res in results:
        failures.extend(res.get("failures") or [])
        warnings.extend(res.get("warnings") or [])
    return failures, warnings, None


async def evaluate(
    policies: Iterable[dict], plan_json: str, *, timeout: int = _EVAL_TIMEOUT
) -> PolicyTestResult:
    """Run each policy's rego against `plan_json` via conftest, one run per policy.

    Per-policy invocation gives clean attribution (which rule fired) and fault
    isolation (a broken rego doesn't sink the batch). Each policy dict needs
    `name`, `rego`, `severity`.
    """
    pols = list(policies)
    if not pols:
        return PolicyTestResult(ok=True, violations=[], warnings=[])

    violations: list[Violation] = []
    warnings: list[Violation] = []
    engine_error: Optional[str] = None

    with tempfile.TemporaryDirectory(prefix="tdt-opa-") as root:
        plan_path = os.path.join(root, "plan.json")
        with open(plan_path, "w") as fh:
            fh.write(plan_json or "{}")

        for idx, pol in enumerate(pols):
            name = pol.get("name") or f"policy-{idx}"
            severity = pol.get("severity") or "block"
            if severity not in SEVERITIES:
                severity = "block"
            pdir = os.path.join(root, f"{idx}_{_safe_dirname(name)}")
            os.makedirs(pdir, exist_ok=True)
            with open(os.path.join(pdir, "policy.rego"), "w") as fh:
                fh.write(pol.get("rego") or "")

            rc, out, err = await _run(
                "test",
                plan_path,
                "--policy",
                pdir,
                "--output",
                "json",
                "--no-color",
                "--all-namespaces",
                timeout=timeout,
            )
            # rc 0 = clean, 1 = findings; 2+ = engine error (bad rego, etc.).
            if rc >= 2:
                engine_error = (err or out or "conftest error").strip()
                logger.warning("conftest error for policy %s: %s", name, engine_error)
                continue
            fails, warns, parse_err = _parse_conftest(out)
            if parse_err:
                engine_error = parse_err
                continue
            for f in fails:
                violations.append(
                    Violation(
                        policy=name,
                        severity=severity,
                        level="deny",
                        msg=str(f.get("msg") or ""),
                        resource=_resource_of(f),
                    )
                )
            for w in warns:
                warnings.append(
                    Violation(
                        policy=name,
                        severity=severity,
                        level="warn",
                        msg=str(w.get("msg") or ""),
                        resource=_resource_of(w),
                    )
                )

    ok = not any(v.severity == "block" for v in violations)
    return PolicyTestResult(
        ok=ok, violations=violations, warnings=warnings, engine_error=engine_error
    )


async def verify(
    rego: str, tests_rego: str, *, timeout: int = _EVAL_TIMEOUT
) -> PolicyVerifyResult:
    """Run a policy's rego unit tests (`test_*` rules) via `conftest verify`."""
    with tempfile.TemporaryDirectory(prefix="tdt-opa-verify-") as root:
        with open(os.path.join(root, "policy.rego"), "w") as fh:
            fh.write(rego or "")
        with open(os.path.join(root, "policy_test.rego"), "w") as fh:
            fh.write(tests_rego or "")
        rc, out, err = await _run(
            "verify",
            "--policy",
            root,
            "--output",
            "json",
            "--no-color",
            "--all-namespaces",
            timeout=timeout,
        )
    if rc >= 2 or rc == 127 or rc == 124:
        return PolicyVerifyResult(
            ok=False, engine_error=(err or out or "conftest error").strip()
        )
    failures, _warnings, parse_err = _parse_conftest(out)
    if parse_err:
        return PolicyVerifyResult(ok=False, engine_error=parse_err)
    try:
        passed = sum((res.get("successes") or 0) for res in json.loads(out or "[]"))
    except json.JSONDecodeError:
        passed = 0
    return PolicyVerifyResult(
        ok=not failures,
        passed=passed,
        failures=[str(f.get("msg") or "") for f in failures],
    )


# ─── executor bundle ─────────────────────────────────────────────────────────


async def bundle_for_run(db: AsyncSession, bu_id: str) -> list[dict]:
    """The enabled policies for a BU, shaped for the executor to write to disk."""
    return [
        {"name": p.name, "rego": p.rego, "severity": p.severity}
        for p in await list_enabled(db, bu_id)
    ]
