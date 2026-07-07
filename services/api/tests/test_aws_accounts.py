"""Phase-8: configurable AWS accounts with encrypted credentials at rest."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_bu")

from app.models.business_unit import DEFAULT_BU_ID

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_aws_account_encrypts_credentials(
    auth_client, seeded_users, _setup_db
):
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    body = {
        "account_id": "111111111111",
        "name": "example-prod",
        "description": "production AWS account",
        "state_bucket": "example-tfstate-prod",
        "state_bucket_region": "us-east-1",
        "default_region": "us-east-1",
        "access_key_id": "AKIAEXAMPLE0001",
        "secret_access_key": "supersecret-do-not-leak",
    }
    r = await auth_client.post(
        "/api/v1/aws-accounts",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    # Plaintext credentials must NEVER appear in API responses.
    assert "access_key_id" not in out
    assert "secret_access_key" not in out
    assert "supersecret" not in r.text
    # Masked tail is for display only.
    assert out["access_key_id_masked"].endswith("0001")

    # On-disk row must be encrypted (not the plaintext value).
    from app.models.aws_account import AwsAccount
    factory = _setup_db
    async with factory() as session:
        row = (await session.execute(select(AwsAccount))).scalars().first()
        assert row is not None
        assert "supersecret" not in row.secret_access_key_encrypted
        assert "AKIAEXAMPLE0001" not in row.access_key_id_encrypted

        # Round-trip via the service must give the original plaintext back.
        from app.services import aws_account_service as accs
        creds = await accs.list_account_credentials(session, "111111111111")
        assert creds == ("AKIAEXAMPLE0001", "supersecret-do-not-leak")


@pytest.mark.asyncio
async def test_aws_account_id_must_be_unique(auth_client, seeded_users, _setup_db):
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    body = {
        "account_id": "123456789012",
        "name": "soft-prod",
        "state_bucket": "example-tfstate-soft",
        "access_key_id": "AKIAEXAMPLE",
        "secret_access_key": "secret-bytes",
    }
    r1 = await auth_client.post("/api/v1/aws-accounts", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 201
    r2 = await auth_client.post("/api/v1/aws-accounts", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_state_path_mirrors_workspace_tf_working_dir(_setup_db):
    """The S3 key MUST equal `tf_working_dir/terraform.tfstate` so the bucket
    layout mirrors the git layout exactly. Per-account bucket — no shared prefix.
    """
    from app.models.workspace import Workspace
    from app.routers.state import _service_for

    factory = _setup_db
    import uuid
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        # No AwsAccount row registered → falls back to env-bucket; the key still
        # mirrors tf_working_dir.
        ws = Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name="region-shared-resources",
            aws_account_id="111111111111", region="eu-central-1", environment="shared",
            tf_working_dir="account-111111111111/eu-central-1/region-shared-resources",
            repo_url="https://example.com/x.git",
        )
        session.add(ws)
        await session.commit()
        await session.refresh(ws)

        _, key = await _service_for(ws, session)
        assert key == "account-111111111111/eu-central-1/region-shared-resources/terraform.tfstate"


@pytest.mark.asyncio
async def test_state_uses_per_account_bucket_when_configured(monkeypatch, _setup_db):
    """When an AwsAccount row exists, _service_for binds the S3 client to that
    account's bucket and decrypted credentials.
    """
    from app.models.aws_account import AwsAccount
    from app.models.workspace import Workspace
    from app.routers.state import _service_for
    from app.services import aws_account_service as accs

    factory = _setup_db
    import uuid
    async with factory() as session:
        acc_pk = str(uuid.uuid4())
        session.add(AwsAccount(
            business_unit_id=DEFAULT_BU_ID,
            id=acc_pk,
            account_id="333333333333",
            name="example-soft",
            state_bucket="example-tfstate-soft",
            state_bucket_region="us-east-1",
            default_region="us-east-1",
            access_key_id_encrypted=accs.encrypt_secret("AKIASOFT1"),
            secret_access_key_encrypted=accs.encrypt_secret("softsecret"),
        ))
        ws_id = str(uuid.uuid4())
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name="prod",
            aws_account_id="333333333333", region="us-east-1", environment="prod",
            tf_working_dir="account-333333333333/us-east-1/prod",
            repo_url="https://example.com/x.git",
        ))
        await session.commit()

        ws = await session.get(Workspace, ws_id)
        svc, key = await _service_for(ws, session)
        assert svc.bucket == "example-tfstate-soft"
        assert key == "account-333333333333/us-east-1/prod/terraform.tfstate"
