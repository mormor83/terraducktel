"""Pydantic schemas for OPA/conftest policies.

`PolicyResponse` returns the full rego (policies are not secret — they're rules,
authored and read by admins). The test/verify schemas back the two synchronous
authoring loops: dry-run a rule against a real plan, and run its rego unit tests.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["block", "warn", "info"]


class PolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    rego: str = Field(min_length=1)
    tests_rego: Optional[str] = None
    severity: Severity = "block"
    enabled: bool = True


class PolicyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    rego: Optional[str] = Field(default=None, min_length=1)
    tests_rego: Optional[str] = None
    severity: Optional[Severity] = None
    enabled: Optional[bool] = None


class PolicyResponse(BaseModel):
    id: str
    business_unit_id: str
    name: str
    description: Optional[str] = None
    rego: str
    tests_rego: Optional[str] = None
    severity: Severity
    enabled: bool
    current_version: int
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PolicyVersionResponse(BaseModel):
    id: str
    policy_id: str
    version: int
    name: str
    description: Optional[str] = None
    rego: str
    tests_rego: Optional[str] = None
    severity: Severity
    enabled: bool
    changed_by: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── evaluation (dry-run vs a plan) ──────────────────────────────────────────


class Violation(BaseModel):
    """One conftest finding, attributed back to its source policy.

    `level` is conftest's classification (a `deny` rule → "deny", a `warn` rule
    → "warn"). `severity` is the policy's enforcement weight (block/warn/info),
    which decides whether a `deny` actually fails the run under enforce mode.
    """

    policy: str
    severity: Severity
    level: Literal["deny", "warn"]
    msg: str
    resource: Optional[str] = None


class PolicyTestRequest(BaseModel):
    """Dry-run candidate rego and/or stored policies against a plan.

    Exactly one plan source is required: `run_id` (load that run's plan_json,
    BU-scoped) or `plan_json` (paste raw `terraform show -json` output).
    Policy source is any combination of: `rego` (an ad-hoc candidate rule),
    `policy_ids` (stored policies by id), or — if both are omitted — every
    enabled policy in the BU.
    """

    rego: Optional[str] = None
    rego_name: str = "candidate"
    rego_severity: Severity = "block"
    policy_ids: Optional[list[str]] = None
    run_id: Optional[str] = None
    plan_json: Optional[str] = None


class PolicyTestResult(BaseModel):
    # True when nothing that would block under enforce mode fired (no `deny`
    # from a `block`-severity policy).
    ok: bool
    violations: list[Violation] = []
    warnings: list[Violation] = []
    # Non-null when conftest itself failed (bad rego, binary missing, timeout).
    engine_error: Optional[str] = None


# ─── verification (rego unit tests) ──────────────────────────────────────────


class PolicyVerifyRequest(BaseModel):
    rego: str = Field(min_length=1)
    tests_rego: str = Field(min_length=1)


class PolicyVerifyResult(BaseModel):
    ok: bool
    passed: int = 0
    failures: list[str] = []
    engine_error: Optional[str] = None
