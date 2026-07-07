"""CRUD + encryption + merge for global, workspace, and per-run variables.

Encryption mirrors `aws_account_service`: HKDF-derived Fernet key over
`CREDENTIAL_ENCRYPTION_KEY`. Distinct salt so a leaked variable ciphertext
can't be replayed as an AWS-credential ciphertext (and vice versa) even
though both schemes share the master key.

Three scopes layered at executor launch — global ← workspace ← run, last
wins per key — via `get_merged_for_run`. Run-scope values are serialized
into a single JSON object encrypted as one Fernet token and persisted on
`Run.variables_encrypted` (one decrypt per launch, no row join).
"""
from __future__ import annotations

import base64
import json
import uuid
from typing import Iterable, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.run import Run
from app.models.variable import GlobalVariable, WorkspaceVariable
from app.schemas.variable import RunVariable, VariableCreate, VariableUpdate


# ─── crypto ────────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    key = get_credential_encryption_key()
    if len(key) < 16:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        # Distinct salt from aws-credentials so cross-scheme replay is impossible.
        salt=b"terraducktel-variables-v1",
        info=b"fernet-key",
    ).derive(key)
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Variable decryption failed (key rotated?)") from e


def mask_tail(plain: str) -> str:
    """Return `…last4` for UI display — same UX pattern as AWS access keys."""
    if not plain:
        return ""
    return f"…{plain[-4:]}" if len(plain) >= 4 else "***"


# ─── global variables ──────────────────────────────────────────────────────

async def list_globals(
    session: AsyncSession, business_unit_id: str | None = None
) -> list[GlobalVariable]:
    """Global vars for a BU. `business_unit_id=None` returns every BU's (used
    only by the superadmin 'all' list view) — run-time callers always pass a BU."""
    stmt = select(GlobalVariable).order_by(GlobalVariable.key)
    if business_unit_id is not None:
        stmt = stmt.where(GlobalVariable.business_unit_id == business_unit_id)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def get_global_by_id(
    session: AsyncSession, var_id: str, business_unit_id: str | None = None
) -> Optional[GlobalVariable]:
    """Fetch by id, optionally scoped to a BU so one BU can't read/edit another
    BU's global var by guessing its id."""
    row = await session.get(GlobalVariable, var_id)
    if row is None:
        return None
    if business_unit_id is not None and row.business_unit_id != business_unit_id:
        return None
    return row


async def create_global(
    session: AsyncSession, body: VariableCreate, business_unit_id: str
) -> GlobalVariable:
    row = GlobalVariable(
        id=str(uuid.uuid4()),
        business_unit_id=business_unit_id,
        key=body.key,
        value_encrypted=encrypt_value(body.value),
        is_secret=body.is_secret,
        is_hcl=body.is_hcl,
        description=body.description,
    )
    session.add(row)
    return row


async def update_global(
    session: AsyncSession, row: GlobalVariable, body: VariableUpdate
) -> GlobalVariable:
    data = body.model_dump(exclude_unset=True)
    new_value = data.pop("value", None)
    if new_value is not None:
        row.value_encrypted = encrypt_value(new_value)
    for k, v in data.items():
        setattr(row, k, v)
    return row


# ─── workspace variables ───────────────────────────────────────────────────

async def list_for_workspace(
    session: AsyncSession, workspace_id: str
) -> list[WorkspaceVariable]:
    rows = await session.execute(
        select(WorkspaceVariable)
        .where(WorkspaceVariable.workspace_id == workspace_id)
        .order_by(WorkspaceVariable.key)
    )
    return list(rows.scalars().all())


async def get_workspace_var_by_id(
    session: AsyncSession, var_id: str
) -> Optional[WorkspaceVariable]:
    return await session.get(WorkspaceVariable, var_id)


async def create_workspace_var(
    session: AsyncSession, workspace_id: str, body: VariableCreate
) -> WorkspaceVariable:
    row = WorkspaceVariable(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        key=body.key,
        value_encrypted=encrypt_value(body.value),
        is_secret=body.is_secret,
        is_hcl=body.is_hcl,
        description=body.description,
    )
    session.add(row)
    return row


async def update_workspace_var(
    session: AsyncSession, row: WorkspaceVariable, body: VariableUpdate
) -> WorkspaceVariable:
    data = body.model_dump(exclude_unset=True)
    new_value = data.pop("value", None)
    if new_value is not None:
        row.value_encrypted = encrypt_value(new_value)
    for k, v in data.items():
        setattr(row, k, v)
    return row


# ─── run-scope blob ────────────────────────────────────────────────────────

def serialize_run_variables(vars_: Iterable[RunVariable]) -> str:
    """Encrypt a list of RunVariable as a single Fernet token over JSON.

    The wire format is a JSON array of `{key,value,is_secret,is_hcl}` objects;
    one encrypt at trigger time, one decrypt at executor launch. Stored
    verbatim on `Run.variables_encrypted` so the apply phase (post-approval)
    replays the exact same values the planner produced — keeps 4-eyes review
    honest.
    """
    payload = [
        {"key": v.key, "value": v.value, "is_secret": v.is_secret, "is_hcl": v.is_hcl}
        for v in vars_
    ]
    return encrypt_value(json.dumps(payload, separators=(",", ":")))


def deserialize_run_variables(blob: str) -> list[dict]:
    """Inverse of `serialize_run_variables`. Returns the plaintext list of
    `{key,value,is_secret,is_hcl}` dicts. Caller owns the secrets.
    """
    return json.loads(decrypt_value(blob))


# ─── merge for executor ────────────────────────────────────────────────────

class _Merged:
    """Single decrypted variable bound for executor injection.

    Distinguishes hcl-typed from string-typed so the caller can JSON-encode
    HCL values for `TF_VAR_*` env injection.
    """

    __slots__ = ("key", "value", "is_hcl", "is_secret", "source")

    def __init__(self, key: str, value: str, *, is_hcl: bool, is_secret: bool, source: str) -> None:
        self.key = key
        self.value = value
        self.is_hcl = is_hcl
        self.is_secret = is_secret
        self.source = source  # "global" | "workspace" | "run"

    def env_value(self) -> str:
        """Render for `TF_VAR_<key>` env injection.

        Terraform parses values starting with `[`/`{` as HCL; everything else
        is a string. For HCL, we assume the operator supplied valid HCL/JSON
        already — we don't re-quote.
        """
        return self.value


async def get_merged_for_run(
    session: AsyncSession, workspace_id: str, run: Run
) -> dict[str, _Merged]:
    """Build the per-run merged variable map.

    Precedence (lowest → highest): global, workspace, run-scope.

    Globals are scoped to the workspace's own BU — a run must NEVER inject
    another BU's global variables (they're per-BU, and may be secret).
    """
    from app.models.workspace import Workspace

    merged: dict[str, _Merged] = {}

    ws = await session.get(Workspace, workspace_id)
    bu_id = ws.business_unit_id if ws is not None else None
    for row in await list_globals(session, bu_id):
        merged[row.key] = _Merged(
            key=row.key,
            value=decrypt_value(row.value_encrypted),
            is_hcl=row.is_hcl,
            is_secret=row.is_secret,
            source="global",
        )

    for row in await list_for_workspace(session, workspace_id):
        merged[row.key] = _Merged(
            key=row.key,
            value=decrypt_value(row.value_encrypted),
            is_hcl=row.is_hcl,
            is_secret=row.is_secret,
            source="workspace",
        )

    if run.variables_encrypted:
        for entry in deserialize_run_variables(run.variables_encrypted):
            merged[entry["key"]] = _Merged(
                key=entry["key"],
                value=entry["value"],
                is_hcl=bool(entry.get("is_hcl", False)),
                is_secret=bool(entry.get("is_secret", False)),
                source="run",
            )

    return merged
