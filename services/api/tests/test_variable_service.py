"""Unit coverage for variable_service: crypto/masking, global + workspace var
CRUD, run-scope serialize/deserialize, and the global←workspace←run merge."""
import uuid

import pytest

from app.services import variable_service as vs
from app.schemas.variable import RunVariable, VariableCreate, VariableUpdate
from app.models.run import Run, RunStatus
from app.models.workspace import Workspace
from app.models.business_unit import DEFAULT_BU_ID

pytestmark = pytest.mark.usefixtures("default_bu")


# ─── crypto + masking ────────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    enc = vs.encrypt_value("hunter2")
    assert vs.decrypt_value(enc) == "hunter2"


def test_decrypt_invalid_raises():
    with pytest.raises(RuntimeError, match="key rotated"):
        vs.decrypt_value("garbage")


def test_fernet_short_key_raises(monkeypatch):
    monkeypatch.setattr(vs, "get_credential_encryption_key", lambda: b"short")
    with pytest.raises(RuntimeError, match="at least 16 bytes"):
        vs.encrypt_value("x")


@pytest.mark.parametrize("plain,expected", [("", ""), ("abcdef", "…cdef"), ("ab", "***")])
def test_mask_tail(plain, expected):
    assert vs.mask_tail(plain) == expected


# ─── global variable CRUD ────────────────────────────────────────────────────


async def test_global_crud(db_session):
    created = await vs.create_global(
        db_session,
        VariableCreate(key="TF_LOG", value="DEBUG", is_secret=False, is_hcl=False),
        DEFAULT_BU_ID,
    )
    await db_session.commit()

    rows = await vs.list_globals(db_session, DEFAULT_BU_ID)
    assert any(r.key == "TF_LOG" for r in rows)
    assert len(await vs.list_globals(db_session)) >= 1  # all-BU view

    # get by id, BU-scoped + cross-BU rejection + missing
    assert (await vs.get_global_by_id(db_session, created.id, DEFAULT_BU_ID)).key == "TF_LOG"
    assert await vs.get_global_by_id(db_session, created.id, "other-bu") is None
    assert await vs.get_global_by_id(db_session, "missing") is None

    # update value (re-encrypts) + other fields
    await vs.update_global(
        db_session, created, VariableUpdate(value="INFO", description="noisy")
    )
    assert vs.decrypt_value(created.value_encrypted) == "INFO"
    assert created.description == "noisy"


# ─── workspace variable CRUD ─────────────────────────────────────────────────


async def test_workspace_var_crud(db_session):
    ws = Workspace(
        business_unit_id=DEFAULT_BU_ID,
        name="w",
        aws_account_id="123456789012",
        region="us-east-1",
        environment="dev",
    )
    db_session.add(ws)
    await db_session.commit()

    row = await vs.create_workspace_var(
        db_session, ws.id, VariableCreate(key="region", value="eu-west-1")
    )
    await db_session.commit()
    assert [r.key for r in await vs.list_for_workspace(db_session, ws.id)] == ["region"]
    assert (await vs.get_workspace_var_by_id(db_session, row.id)).key == "region"

    await vs.update_workspace_var(db_session, row, VariableUpdate(value="us-west-2", is_hcl=True))
    assert vs.decrypt_value(row.value_encrypted) == "us-west-2"
    assert row.is_hcl is True


# ─── run-scope blob ──────────────────────────────────────────────────────────


def test_serialize_deserialize_run_variables():
    blob = vs.serialize_run_variables(
        [RunVariable(key="a", value="1", is_secret=True, is_hcl=False)]
    )
    out = vs.deserialize_run_variables(blob)
    assert out == [{"key": "a", "value": "1", "is_secret": True, "is_hcl": False}]


def test_merged_env_value_passthrough():
    m = vs._Merged("k", "v", is_hcl=True, is_secret=False, source="run")
    assert m.env_value() == "v"


# ─── merge precedence ────────────────────────────────────────────────────────


async def test_get_merged_for_run_precedence(db_session):
    ws = Workspace(
        business_unit_id=DEFAULT_BU_ID,
        name="merge-ws",
        aws_account_id="123456789012",
        region="us-east-1",
        environment="dev",
    )
    db_session.add(ws)
    await db_session.commit()

    # global sets a + shared; workspace overrides shared + adds b; run overrides all of shared.
    await vs.create_global(db_session, VariableCreate(key="a", value="g"), DEFAULT_BU_ID)
    await vs.create_global(db_session, VariableCreate(key="shared", value="g"), DEFAULT_BU_ID)
    await vs.create_workspace_var(db_session, ws.id, VariableCreate(key="shared", value="w"))
    await vs.create_workspace_var(db_session, ws.id, VariableCreate(key="b", value="w"))
    await db_session.commit()

    run = Run(
        id=str(uuid.uuid4()),
        workspace_id=ws.id,
        command="plan",
        status=RunStatus.PENDING,
        variables_encrypted=vs.serialize_run_variables(
            [RunVariable(key="shared", value="r", is_secret=False, is_hcl=False)]
        ),
    )
    db_session.add(run)
    await db_session.commit()

    merged = await vs.get_merged_for_run(db_session, ws.id, run)
    assert merged["a"].value == "g" and merged["a"].source == "global"
    assert merged["b"].value == "w" and merged["b"].source == "workspace"
    assert merged["shared"].value == "r" and merged["shared"].source == "run"


async def test_get_merged_for_run_no_run_vars(db_session):
    ws = Workspace(
        business_unit_id=DEFAULT_BU_ID,
        name="norun-ws",
        aws_account_id="123456789012",
        region="us-east-1",
        environment="dev",
    )
    db_session.add(ws)
    await db_session.commit()
    run = Run(id=str(uuid.uuid4()), workspace_id=ws.id, command="plan", status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    merged = await vs.get_merged_for_run(db_session, ws.id, run)
    assert merged == {}
