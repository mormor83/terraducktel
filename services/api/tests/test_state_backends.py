"""Unit tests for the pluggable StateStore backends (Azure Blob + GCS).

Assert the StateStore contract that routers/state.py depends on:
  - get_state_at: bytes when present, None when absent, RAISES on other errors
  - delete_state_at: True on delete-or-already-absent, RAISES on other errors
  - put_state_at: persists bytes; no SSE-specific coupling

Real cloud clients are replaced with in-memory fakes via the module-level
`_blob_service` / `_client` factory functions.
"""
import pytest

from azure.core.exceptions import ResourceNotFoundError
from google.cloud.exceptions import NotFound

from app.services import azure_blob_state_service as az
from app.services import gcs_state_service as gcs
from app.services.state_store import StateStore


# --------------------------------------------------------------------------
# Azure Blob
# --------------------------------------------------------------------------
class _FakeAzureBlob:
    def __init__(self, store, key):
        self._store, self._key = store, key

    def download_blob(self):
        if self._key not in self._store:
            raise ResourceNotFoundError("nope")
        data = self._store[self._key]

        class _D:
            def readall(_self):
                return data

        return _D()

    def upload_blob(self, data, overwrite=False):
        self._store[self._key] = data

    def delete_blob(self):
        if self._key not in self._store:
            raise ResourceNotFoundError("nope")
        del self._store[self._key]


class _FakeAzureService:
    def __init__(self, store):
        self._store = store

    def get_blob_client(self, container, key):
        return _FakeAzureBlob(self._store, key)


@pytest.fixture
def azure_store(monkeypatch):
    backing: dict[str, bytes] = {}
    monkeypatch.setattr(az, "_blob_service", lambda *a, **k: _FakeAzureService(backing))
    return az.AzureBlobStateService("acct", "cont", "t", "c", "s"), backing


def test_azure_implements_protocol(azure_store):
    svc, _ = azure_store
    assert isinstance(svc, StateStore)


def test_azure_roundtrip(azure_store):
    svc, _ = azure_store
    assert svc.get_state_at("k/terraform.tfstate") is None            # absent → None (→ 404)
    svc.put_state_at("k/terraform.tfstate", b'{"v":1}')
    assert svc.get_state_at("k/terraform.tfstate") == b'{"v":1}'
    assert svc.delete_state_at("k/terraform.tfstate") is True
    assert svc.delete_state_at("k/terraform.tfstate") is True         # already-absent → True
    assert svc.get_state_at("k/terraform.tfstate") is None


def test_azure_get_reraises_unknown(monkeypatch):
    class _BoomBlob:
        def download_blob(self):
            raise ValueError("network")

    class _BoomSvc:
        def get_blob_client(self, c, k):
            return _BoomBlob()

    monkeypatch.setattr(az, "_blob_service", lambda *a, **k: _BoomSvc())
    svc = az.AzureBlobStateService("a", "b", "t", "c", "s")
    with pytest.raises(ValueError):  # non-not-found errors must surface (→ 503)
        svc.get_state_at("k")


# --------------------------------------------------------------------------
# GCS
# --------------------------------------------------------------------------
class _FakeGcsBlob:
    def __init__(self, store, name):
        self._store, self._name = store, name

    def download_as_bytes(self):
        if self._name not in self._store:
            raise NotFound("nope")
        return self._store[self._name]

    def upload_from_string(self, data, content_type=None):
        self._store[self._name] = data if isinstance(data, bytes) else data.encode()

    def delete(self):
        if self._name not in self._store:
            raise NotFound("nope")
        del self._store[self._name]


class _FakeGcsBucket:
    def __init__(self, store):
        self._store = store

    def blob(self, name):
        return _FakeGcsBlob(self._store, name)


class _FakeGcsClient:
    def __init__(self, store):
        self._store = store

    def bucket(self, name):
        return _FakeGcsBucket(self._store)


@pytest.fixture
def gcs_store(monkeypatch):
    backing: dict[str, bytes] = {}
    monkeypatch.setattr(gcs, "_client", lambda *a, **k: _FakeGcsClient(backing))
    return gcs.GcsStateService("bkt", "{}", "proj", prefix=""), backing


def test_gcs_implements_protocol(gcs_store):
    svc, _ = gcs_store
    assert isinstance(svc, StateStore)


def test_gcs_roundtrip(gcs_store):
    svc, _ = gcs_store
    assert svc.get_state_at("k/terraform.tfstate") is None
    svc.put_state_at("k/terraform.tfstate", b'{"v":2}')
    assert svc.get_state_at("k/terraform.tfstate") == b'{"v":2}'
    assert svc.delete_state_at("k/terraform.tfstate") is True
    assert svc.delete_state_at("k/terraform.tfstate") is True


def test_gcs_prefix_applied(monkeypatch):
    backing: dict[str, bytes] = {}
    monkeypatch.setattr(gcs, "_client", lambda *a, **k: _FakeGcsClient(backing))
    svc = gcs.GcsStateService("bkt", "{}", "proj", prefix="team-a/")
    svc.put_state_at("k/terraform.tfstate", b"x")
    assert "team-a/k/terraform.tfstate" in backing


def test_gcs_get_reraises_unknown(monkeypatch):
    class _BoomBlob:
        def download_as_bytes(self):
            raise ValueError("network")

    class _BoomBucket:
        def blob(self, n):
            return _BoomBlob()

    class _BoomClient:
        def bucket(self, n):
            return _BoomBucket()

    monkeypatch.setattr(gcs, "_client", lambda *a, **k: _BoomClient())
    svc = gcs.GcsStateService("b", "{}", "p")
    with pytest.raises(ValueError):
        svc.get_state_at("k")
