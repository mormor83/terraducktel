#!/usr/bin/env python3
"""Seed a few common OPA/conftest policies for a Business Unit (idempotent).

Run after: alembic upgrade head

  DATABASE_URL=postgresql+asyncpg://... python scripts/seed_policies.py [bu_slug]

Defaults to the seeded 'default' BU. Each policy is created via the policy
service so it gets a v1 snapshot + an audit-log entry exactly like a UI create.
Existing policies (matched by name within the BU) are left untouched, so re-runs
are safe. These are starter examples — operators can edit/disable them in
Settings → Policies.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Ensure app is importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.business_unit import BusinessUnit  # noqa: E402
from app.models.policy import Policy  # noqa: E402

# Importing these registers their tables so SQLAlchemy can resolve the
# audit_logs.user_id FK (written by policy_service) at mapper-configure time.
import app.models.user  # noqa: E402,F401
import app.models.audit_log  # noqa: E402,F401
from app.schemas.policy import PolicyCreate  # noqa: E402
from app.services import policy_service  # noqa: E402

# (name, severity, description, rego, tests_rego)
#
# NOTE on rego style: conftest collects members of the `deny` / `warn` SETS.
# Use the classic partial-set form `deny[msg] { ... }` (no `if`). The newer
# `deny[msg] if { ... }` form (with future.keywords.if) is parsed as a complete
# rule, NOT a set member, so conftest never sees the finding. Test rules in
# tests_rego may use `test_x if { ... }` — that's fine for `conftest verify`.
COMMON_POLICIES: tuple[tuple[str, str, str, str, str | None], ...] = (
    (
        "require-tags",
        "warn",
        "Every tagged resource must carry Environment and ManagedBy tags.",
        """package main

required := {"Environment", "ManagedBy"}

deny[msg] {
\trc := input.resource_changes[_]
\ttags := rc.change.after.tags
\ttags != null
\tt := required[_]
\tnot tags[t]
\tmsg := sprintf("%s is missing required tag %q", [rc.address, t])
}
""",
        None,
    ),
    (
        "deny-public-s3",
        "block",
        "S3 buckets must block public ACLs (aws_s3_bucket_public_access_block).",
        """package main

deny[msg] {
\trc := input.resource_changes[_]
\trc.type == "aws_s3_bucket_public_access_block"
\trc.change.after.block_public_acls == false
\tmsg := sprintf("%s allows public ACLs", [rc.address])
}
""",
        """package main

import future.keywords.if

test_flags_public_block if {
\tdeny[_] with input as {"resource_changes": [{
\t\t"address": "aws_s3_bucket_public_access_block.x",
\t\t"type": "aws_s3_bucket_public_access_block",
\t\t"change": {"after": {"block_public_acls": false}},
\t}]}
}

test_allows_private_block if {
\tcount(deny) == 0 with input as {"resource_changes": [{
\t\t"address": "aws_s3_bucket_public_access_block.x",
\t\t"type": "aws_s3_bucket_public_access_block",
\t\t"change": {"after": {"block_public_acls": true}},
\t}]}
}
""",
    ),
    (
        "deny-open-ingress",
        "block",
        "Security groups must not open ingress to 0.0.0.0/0.",
        """package main

# aws_security_group_rule resources.
deny[msg] {
\trc := input.resource_changes[_]
\trc.type == "aws_security_group_rule"
\trc.change.after.type == "ingress"
\trc.change.after.cidr_blocks[_] == "0.0.0.0/0"
\tmsg := sprintf("%s opens ingress to 0.0.0.0/0", [rc.address])
}

# Inline ingress blocks on aws_security_group.
deny[msg] {
\trc := input.resource_changes[_]
\trc.type == "aws_security_group"
\tingress := rc.change.after.ingress[_]
\tingress.cidr_blocks[_] == "0.0.0.0/0"
\tmsg := sprintf("%s has an inline ingress rule open to 0.0.0.0/0", [rc.address])
}
""",
        None,
    ),
    (
        "require-rds-encryption",
        "warn",
        "RDS instances must have storage encryption enabled.",
        """package main

deny[msg] {
\trc := input.resource_changes[_]
\trc.type == "aws_db_instance"
\trc.change.after.storage_encrypted == false
\tmsg := sprintf("%s has storage encryption disabled", [rc.address])
}
""",
        None,
    ),
)


async def seed(bu_slug: str) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        return 1

    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        bu = (
            await session.execute(select(BusinessUnit).where(BusinessUnit.slug == bu_slug))
        ).scalars().first()
        if bu is None:
            print(f"ERROR: business unit '{bu_slug}' not found", file=sys.stderr)
            await engine.dispose()
            return 1

        for name, severity, description, rego, tests_rego in COMMON_POLICIES:
            existing = (
                await session.execute(
                    select(Policy).where(
                        Policy.business_unit_id == bu.id, Policy.name == name
                    )
                )
            ).scalars().first()
            if existing is not None:
                print(f"skip (exists): {name}")
                continue
            await policy_service.create_policy(
                session,
                bu.id,
                PolicyCreate(
                    name=name,
                    description=description,
                    rego=rego,
                    tests_rego=tests_rego,
                    severity=severity,  # type: ignore[arg-type]
                    enabled=True,
                ),
                user_id=None,
            )
            print(f"created: {name} ({severity})")

    await engine.dispose()
    print("done.")
    return 0


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "default"
    raise SystemExit(asyncio.run(seed(slug)))
