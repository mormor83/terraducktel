"""Router coverage for /api/v1/aws-accounts: CRUD + test-connection + bucket
creation (boto3 mocked) + the _to_response unreadable-key branch."""
import boto3
import pytest
from botocore.exceptions import ClientError

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


def _body(account_id="111111111111", **over):
    b = {
        "account_id": account_id,
        "name": f"acct-{account_id}",
        "state_bucket": f"bkt-{account_id}",
        "state_bucket_region": "us-east-1",
        "default_region": "us-east-1",
        "access_key_id": "AKIAEXAMPLE123",
        "secret_access_key": "secret",
    }
    b.update(over)
    return b


async def _create(client, token, **over):
    r = await client.post("/api/v1/aws-accounts", json=_body(**over), headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


# ─── CRUD ────────────────────────────────────────────────────────────────────


async def test_create_list_update_delete(auth_client, admin_token):
    acc = await _create(auth_client, admin_token)
    assert acc["access_key_id_masked"].startswith("AKIA")

    lst = await auth_client.get("/api/v1/aws-accounts", headers=_h(admin_token))
    assert lst.status_code == 200 and any(a["id"] == acc["id"] for a in lst.json())

    upd = await auth_client.put(
        f"/api/v1/aws-accounts/{acc['id']}",
        json={"name": "renamed", "access_key_id": "AKIANEWKEY999", "secret_access_key": "s2"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "renamed"

    d = await auth_client.delete(f"/api/v1/aws-accounts/{acc['id']}", headers=_h(admin_token))
    assert d.status_code == 204


async def test_create_duplicate_409(auth_client, admin_token):
    await _create(auth_client, admin_token, account_id="222222222222")
    dup = await auth_client.post(
        "/api/v1/aws-accounts", json=_body(account_id="222222222222"), headers=_h(admin_token)
    )
    assert dup.status_code == 409


async def test_update_delete_404(auth_client, admin_token):
    assert (
        await auth_client.put(
            "/api/v1/aws-accounts/missing", json={"name": "x"}, headers=_h(admin_token)
        )
    ).status_code == 404
    assert (
        await auth_client.delete("/api/v1/aws-accounts/missing", headers=_h(admin_token))
    ).status_code == 404


async def test_create_requires_concrete_bu_for_superadmin(auth_client, admin_token, _setup_db):
    # Make admin a superadmin and send header 'all' → BUScope.bu_id is None → 400.
    from app.models.user import User
    from sqlalchemy import select

    async with _setup_db() as s:
        u = (await s.execute(select(User).where(User.email == "admin@test.com"))).scalars().first()
        u.is_superadmin = True
        await s.commit()
    r = await auth_client.post(
        "/api/v1/aws-accounts", json=_body(), headers=_h(admin_token, bu="all")
    )
    assert r.status_code == 400


# ─── _to_response unreadable key ─────────────────────────────────────────────


async def test_list_marks_unreadable_key(auth_client, admin_token, _setup_db):
    acc = await _create(auth_client, admin_token, account_id="333333333333")
    # Corrupt the ciphertext so decrypt fails → masked shows "(unreadable)".
    from app.models.aws_account import AwsAccount

    async with _setup_db() as s:
        row = await s.get(AwsAccount, acc["id"])
        row.access_key_id_encrypted = "not-a-valid-token"
        await s.commit()
    lst = await auth_client.get("/api/v1/aws-accounts", headers=_h(admin_token))
    target = next(a for a in lst.json() if a["id"] == acc["id"])
    assert target["access_key_id_masked"] == "(unreadable)"


# ─── test-connection + bucket (boto3 mocked) ─────────────────────────────────


class _FakeSts:
    def get_caller_identity(self):
        return {"Arn": "arn:aws:iam::111:user/test"}


class _FakeS3:
    def __init__(self, *, head_ok=True, fail_hardening=False, fail_create=False):
        self.head_ok = head_ok
        self.fail_hardening = fail_hardening
        self.fail_create = fail_create
        self.created = []

    def head_bucket(self, Bucket):
        if not self.head_ok:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, **kw):
        if self.fail_create:
            raise RuntimeError("create denied")
        self.created.append(kw)

    def _maybe_fail(self):
        if self.fail_hardening:
            raise RuntimeError("hardening denied")

    def put_bucket_versioning(self, **kw):
        self._maybe_fail()

    def put_bucket_encryption(self, **kw):
        self._maybe_fail()

    def put_public_access_block(self, **kw):
        self._maybe_fail()


def _install_boto(monkeypatch, sts=None, s3=None):
    def factory(service, **kw):
        return sts if service == "sts" else s3

    monkeypatch.setattr(boto3, "client", factory)


async def test_test_connection_ok_and_failure(auth_client, admin_token, monkeypatch):
    acc = await _create(auth_client, admin_token, account_id="444444444444")
    _install_boto(monkeypatch, sts=_FakeSts(), s3=_FakeS3(head_ok=True))
    ok = await auth_client.post(f"/api/v1/aws-accounts/{acc['id']}/test", headers=_h(admin_token))
    assert ok.status_code == 200 and ok.json()["ok"] is True and ok.json()["bucket_exists"] is True

    # bucket missing → bucket_exists False (head_bucket raises inside try)
    _install_boto(monkeypatch, sts=_FakeSts(), s3=_FakeS3(head_ok=False))
    miss = await auth_client.post(f"/api/v1/aws-accounts/{acc['id']}/test", headers=_h(admin_token))
    assert miss.json()["bucket_exists"] is False

    # sts raises → ok False
    class _BadSts:
        def get_caller_identity(self):
            raise RuntimeError("bad creds")

    _install_boto(monkeypatch, sts=_BadSts(), s3=_FakeS3())
    bad = await auth_client.post(f"/api/v1/aws-accounts/{acc['id']}/test", headers=_h(admin_token))
    assert bad.json()["ok"] is False


async def test_test_connection_404(auth_client, admin_token):
    r = await auth_client.post("/api/v1/aws-accounts/missing/test", headers=_h(admin_token))
    assert r.status_code == 404


async def test_create_bucket_paths(auth_client, admin_token, monkeypatch):
    # already exists
    acc = await _create(auth_client, admin_token, account_id="555555555555")
    _install_boto(monkeypatch, s3=_FakeS3(head_ok=True))
    r = await auth_client.post(f"/api/v1/aws-accounts/{acc['id']}/bucket", headers=_h(admin_token))
    assert r.json()["already_existed"] is True

    # created fresh in a non-us-east-1 region (LocationConstraint branch)
    acc2 = await _create(
        auth_client, admin_token, account_id="666666666666", state_bucket_region="eu-west-1"
    )
    s3 = _FakeS3(head_ok=False)
    _install_boto(monkeypatch, s3=s3)
    r2 = await auth_client.post(f"/api/v1/aws-accounts/{acc2['id']}/bucket", headers=_h(admin_token))
    assert r2.json()["already_existed"] is False
    assert "CreateBucketConfiguration" in s3.created[0]

    # hardening failure still returns ok
    acc3 = await _create(auth_client, admin_token, account_id="777777777777")
    _install_boto(monkeypatch, s3=_FakeS3(head_ok=False, fail_hardening=True))
    r3 = await auth_client.post(f"/api/v1/aws-accounts/{acc3['id']}/bucket", headers=_h(admin_token))
    assert r3.json()["ok"] is True

    # create failure → ok False
    acc4 = await _create(auth_client, admin_token, account_id="888888888888")
    _install_boto(monkeypatch, s3=_FakeS3(head_ok=False, fail_create=True))
    r4 = await auth_client.post(f"/api/v1/aws-accounts/{acc4['id']}/bucket", headers=_h(admin_token))
    assert r4.json()["ok"] is False


async def test_create_bucket_404(auth_client, admin_token):
    r = await auth_client.post("/api/v1/aws-accounts/missing/bucket", headers=_h(admin_token))
    assert r.status_code == 404


async def test_create_commit_race_returns_409(auth_client, admin_token, _setup_db):
    """If commit raises (another caller inserted the same id between SELECT and
    INSERT), the route rolls back and surfaces 409. Force it via a get_db
    override whose first commit() raises."""
    from app.main import app
    from app.db import get_db

    class _CommitFailOnce:
        def __init__(self, real):
            self._real = real
            self._failed = False

        def __getattr__(self, name):
            return getattr(self._real, name)

        async def commit(self):
            if not self._failed:
                self._failed = True
                raise RuntimeError("simulated race")
            return await self._real.commit()

    async def _override():
        async with _setup_db() as s:
            yield _CommitFailOnce(s)

    app.dependency_overrides[get_db] = _override
    try:
        r = await auth_client.post(
            "/api/v1/aws-accounts", json=_body(account_id="999999999999"), headers=_h(admin_token)
        )
        assert r.status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)
