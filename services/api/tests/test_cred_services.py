"""Unit coverage for the credential service layer: aws_account_service,
azure_subscription_service (Fernet roundtrips + masking + DB lookups), and
S3StateService (boto3 mocked)."""
import pytest

from app.services import aws_account_service as aws
from app.services import azure_subscription_service as az
from app.services import s3_state_service as s3mod
from app.models.aws_account import AwsAccount
from app.models.azure_subscription import AzureSubscription
from app.models.business_unit import DEFAULT_BU_ID

pytestmark = pytest.mark.usefixtures("default_bu")


# ─── aws_account_service ─────────────────────────────────────────────────────


def test_aws_encrypt_decrypt_roundtrip():
    enc = aws.encrypt_secret("AKIAEXAMPLE")
    assert enc != "AKIAEXAMPLE"
    assert aws.decrypt_secret(enc) == "AKIAEXAMPLE"


def test_aws_decrypt_invalid_raises():
    with pytest.raises(RuntimeError, match="decryption failed"):
        aws.decrypt_secret("not-a-token")


def test_aws_fernet_short_key_raises(monkeypatch):
    monkeypatch.setattr(aws, "get_credential_encryption_key", lambda: b"short")
    with pytest.raises(RuntimeError, match="at least 16 bytes"):
        aws.encrypt_secret("x")


@pytest.mark.parametrize(
    "plain,expected",
    [("", ""), ("AKIA12345678", "AKIA…5678"), ("abc", "***")],
)
def test_mask_access_key_tail(plain, expected):
    assert aws.mask_access_key_tail(plain) == expected


async def test_aws_account_lookups_and_credentials(db_session):
    acc = AwsAccount(
        business_unit_id=DEFAULT_BU_ID,
        account_id="999999999999",
        name="acc",
        state_bucket="b",
        access_key_id_encrypted=aws.encrypt_secret("AKIAKEY"),
        secret_access_key_encrypted=aws.encrypt_secret("secret"),
    )
    db_session.add(acc)
    await db_session.commit()

    # by id, with + without BU filter
    assert (await aws.get_account_by_account_id(db_session, "999999999999")).id == acc.id
    assert (
        await aws.get_account_by_account_id(db_session, "999999999999", DEFAULT_BU_ID)
    ).id == acc.id
    assert await aws.get_account_by_account_id(db_session, "111111111111") is None

    # list with + without BU filter
    assert any(a.account_id == "999999999999" for a in await aws.list_accounts(db_session))
    assert len(await aws.list_accounts(db_session, DEFAULT_BU_ID)) >= 1

    # credentials decrypt
    creds = await aws.list_account_credentials(db_session, "999999999999", DEFAULT_BU_ID)
    assert creds == ("AKIAKEY", "secret")
    assert await aws.list_account_credentials(db_session, "000000000001") is None


# ─── azure_subscription_service ──────────────────────────────────────────────


def test_azure_encrypt_decrypt_roundtrip():
    enc = az.encrypt_secret("sp-secret")
    assert az.decrypt_secret(enc) == "sp-secret"


def test_azure_decrypt_invalid_raises():
    with pytest.raises(RuntimeError, match="decryption failed"):
        az.decrypt_secret("garbage")


def test_azure_fernet_missing_key_raises(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="must be set"):
        az.encrypt_secret("x")


def test_azure_fernet_short_key_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "short")
    with pytest.raises(RuntimeError, match="at least 16 bytes"):
        az.encrypt_secret("x")


@pytest.mark.parametrize("plain,expected", [("", ""), ("abcdef", "…cdef"), ("ab", "***")])
def test_azure_mask_secret_tail(plain, expected):
    assert az.mask_secret_tail(plain) == expected


async def test_azure_subscription_lookup_and_credentials(db_session):
    sub = AzureSubscription(
        business_unit_id=DEFAULT_BU_ID,
        subscription_id="sub-1",
        tenant_id="ten-1",
        client_id="cli-1",
        client_secret_encrypted=az.encrypt_secret("sp-secret"),
        name="azure-dev",
        default_location="eastus",
    )
    db_session.add(sub)
    await db_session.commit()

    assert (await az.get_subscription(db_session, sub.id)).id == sub.id
    assert len(await az.list_subscriptions(db_session)) >= 1
    assert len(await az.list_subscriptions(db_session, DEFAULT_BU_ID)) >= 1

    creds = await az.get_subscription_credentials(db_session, sub.id)
    assert creds == ("sub-1", "ten-1", "cli-1", "sp-secret")
    assert await az.get_subscription_credentials(db_session, "missing-pk") is None


# ─── S3StateService (boto3 mocked) ───────────────────────────────────────────


def _client_error(code):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code}}, "GetObject")


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _FakeS3:
    def __init__(self):
        self.calls = []

    def get_object(self, **kw):
        self.calls.append(("get", kw))
        if kw["Key"].endswith("missing/terraform.tfstate") or "missing" in kw["Key"]:
            raise _client_error("NoSuchKey")
        if "boom" in kw["Key"]:
            raise _client_error("AccessDenied")
        return {"Body": _Body(b"STATE")}

    def put_object(self, **kw):
        self.calls.append(("put", kw))

    def delete_object(self, **kw):
        self.calls.append(("delete", kw))
        if "boom" in kw["Key"]:
            raise _client_error("AccessDenied")
        if "gone" in kw["Key"]:
            raise _client_error("NoSuchKey")


@pytest.fixture
def s3(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(s3mod.boto3, "client", lambda *a, **k: fake)
    return fake


def test_s3_init_explicit_creds_and_localstack(s3):
    # exercise the explicit-creds + localstack config branches
    svc = s3mod.S3StateService(
        "bkt", use_localstack=True, access_key_id="AK", secret_access_key="SK"
    )
    assert svc.bucket == "bkt"


def test_s3_get_state_at_hit_miss_and_error(s3):
    svc = s3mod.S3StateService("bkt")
    assert svc.get_state_at("ok/terraform.tfstate") == b"STATE"
    assert svc.get_state_at("missing/terraform.tfstate") is None
    with pytest.raises(Exception):
        svc.get_state_at("boom/terraform.tfstate")


def test_s3_delete_state_at(s3):
    svc = s3mod.S3StateService("bkt")
    assert svc.delete_state_at("ok/x") is True
    assert svc.delete_state_at("gone/x") is True  # NoSuchKey → already gone
    with pytest.raises(Exception):
        svc.delete_state_at("boom/x")


def test_s3_put_and_keyed_get(s3):
    svc = s3mod.S3StateService("bkt")
    svc.put_state_at("k", b"data")
    svc.put_state("acct", "dev", "vpc", b"data", region="us-east-1")
    # state_key with + without region
    assert svc._state_key("a", "dev", "vpc") == "tfstate/a/dev/vpc/terraform.tfstate"
    assert (
        svc._state_key("a", "dev", "vpc", "us-east-1")
        == "tfstate/a/us-east-1/dev/vpc/terraform.tfstate"
    )
    assert svc.get_state("acct", "dev", "vpc", region="us-east-1") == b"STATE"
    assert svc.get_state("acct", "dev", "missing") is None
    with pytest.raises(Exception):
        svc.get_state("acct", "dev", "boom")
