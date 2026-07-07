"""Policies router: BU-scoped OPA/conftest rego rules + the authoring loops.

CRUD + version history + restore (admin), plus two synchronous authoring
endpoints (operator): `test` dry-runs rego against a real plan, `verify` runs a
policy's rego unit tests. Everything is scoped to the caller's current BU via
`current_bu`; superadmins target a BU with `X-Business-Unit`. Writes are
recorded in the tamper-evident audit log (in `policy_service`).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu, scoped_run
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.user import User
from app.schemas.policy import (
    PolicyCreate,
    PolicyResponse,
    PolicyTestRequest,
    PolicyTestResult,
    PolicyUpdate,
    PolicyVerifyRequest,
    PolicyVerifyResult,
    PolicyVersionResponse,
)
from app.services import policy_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])


def _require_bu(bu: BUScope) -> str:
    """Policies are always bound to one concrete BU — reject the 'all' scope."""
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a specific Business Unit (X-Business-Unit) to manage policies",
        )
    return bu.bu_id


@router.get("", response_model=list[PolicyResponse])
async def list_policies(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.list_policies(db, _require_bu(bu))


@router.post("", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.create_policy(db, _require_bu(bu), body, current_user.id)


# NOTE: `test` and `verify` must be declared before `/{policy_id}` so the static
# paths aren't shadowed by the path parameter.
@router.post("/test", response_model=PolicyTestResult)
async def test_policy(
    body: PolicyTestRequest,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run rego against a plan and report violations per resource."""
    bu_id = _require_bu(bu)

    # Resolve the plan source.
    if body.run_id:
        run = await scoped_run(body.run_id, bu, db)
        plan_json = run.plan_json or ""
        if not plan_json:
            raise HTTPException(status_code=400, detail="Run has no captured plan JSON")
    elif body.plan_json:
        plan_json = body.plan_json
    else:
        raise HTTPException(status_code=400, detail="Provide run_id or plan_json")

    # Assemble the policy set: ad-hoc candidate + selected ids, else all enabled.
    policies: list[dict] = []
    if body.rego:
        policies.append(
            {"name": body.rego_name, "rego": body.rego, "severity": body.rego_severity}
        )
    if body.policy_ids:
        for pid in body.policy_ids:
            p = await policy_service.get_policy(db, bu_id, pid)
            policies.append({"name": p.name, "rego": p.rego, "severity": p.severity})
    if not policies:
        policies = await policy_service.bundle_for_run(db, bu_id)

    return await policy_service.evaluate(policies, plan_json)


@router.post("/verify", response_model=PolicyVerifyResult)
async def verify_policy(
    body: PolicyVerifyRequest,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Run a policy's rego unit tests (`test_*` rules) via `conftest verify`."""
    _require_bu(bu)
    return await policy_service.verify(body.rego, body.tests_rego)


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.get_policy(db, _require_bu(bu), policy_id)


@router.put("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: str,
    body: PolicyUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.update_policy(
        db, _require_bu(bu), policy_id, body, current_user.id
    )


@router.delete("/{policy_id}", status_code=status.HTTP_200_OK)
async def delete_policy(
    policy_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    await policy_service.delete_policy(db, _require_bu(bu), policy_id, current_user.id)
    return {"status": "deleted", "id": policy_id}


@router.get("/{policy_id}/versions", response_model=list[PolicyVersionResponse])
async def list_policy_versions(
    policy_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.list_versions(db, _require_bu(bu), policy_id)


@router.post(
    "/{policy_id}/versions/{version}/restore", response_model=PolicyResponse
)
async def restore_policy_version(
    policy_id: str,
    version: int,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.restore_version(
        db, _require_bu(bu), policy_id, version, current_user.id
    )
