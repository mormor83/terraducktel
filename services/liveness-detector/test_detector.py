"""Unit coverage for the liveness detector (httpx mocked, no real GitHub/API)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import detector  # noqa: E402


# ─── _parse_github ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        (None, None),
        ("local:///mnt/repo", None),
        ("https://forgejo.local/o/r.git", None),
        ("https://github.com/octo/infra.git", ("octo", "infra")),
        ("https://github.com/octo/infra", ("octo", "infra")),
        ("git@github.com:octo/infra.git", ("octo", "infra")),
    ],
)
def test_parse_github(url, expected):
    assert detector._parse_github(url) == expected


# ─── _check_path_exists ──────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _GetClient:
    """get() returns a fixed response or raises; used for _check_path_exists."""

    def __init__(self, resp=None, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.last_url = None

    def get(self, url, headers=None, params=None, timeout=None):
        self.last_url = url
        if self.raise_exc:
            raise self.raise_exc
        return self.resp


def test_check_path_exists_200_and_token_header():
    c = _GetClient(_Resp(200))
    exists, status = detector._check_path_exists(c, "o", "r", "envs/dev", "main", "ghtok")
    assert exists is True and status == 200
    assert "contents/envs/dev" in c.last_url


def test_check_path_root_uses_repo_metadata():
    c = _GetClient(_Resp(200))
    detector._check_path_exists(c, "o", "r", ".", "main", "")
    assert c.last_url == "https://api.github.com/repos/o/r"


def test_check_path_404_and_403_and_request_error():
    assert detector._check_path_exists(_GetClient(_Resp(404)), "o", "r", "p", "m", "t") == (False, 404)
    assert detector._check_path_exists(_GetClient(_Resp(403)), "o", "r", "p", "m", "t") == (None, 403)
    err = detector._check_path_exists(
        _GetClient(raise_exc=httpx.RequestError("net", request=None)), "o", "r", "p", "m", "t"
    )
    assert err == (None, -1)


# ─── _scan_once ──────────────────────────────────────────────────────────────


class _ScanClient:
    """Dispatches get() by URL: workspaces list + github-token; records posts."""

    def __init__(self, workspaces, *, token_resp=None, ws_status=200, delete_status=200):
        self._workspaces = workspaces
        self._token_resp = token_resp if token_resp is not None else _Resp(200, {"token": "ght"})
        self._ws_status = ws_status
        self._delete_status = delete_status
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/internal/workspaces"):
            return _Resp(self._ws_status, self._workspaces)
        if url.endswith("/internal/github-token"):
            return self._token_resp
        return _Resp(404)

    def post(self, url, headers=None, json=None):
        self.posts.append((url, json))
        return _Resp(self._delete_status)


def _patch(monkeypatch, client):
    monkeypatch.setattr(detector.httpx, "Client", lambda *a, **k: client)


def test_scan_401_and_non_list(monkeypatch):
    _patch(monkeypatch, _ScanClient([], ws_status=401))
    detector._scan_once("http://api", "tok")
    _patch(monkeypatch, _ScanClient({"bad": "payload"}))
    detector._scan_once("http://api", "tok")


def test_scan_skips_non_github_and_grace_period(monkeypatch):
    fresh = (datetime.now(timezone.utc)).isoformat()
    client = _ScanClient(
        [
            {"id": "w1", "name": "local", "repo_url": "local://x"},
            {"id": "w2", "name": "fresh", "repo_url": "https://github.com/o/r", "created_at": fresh},
        ]
    )
    _patch(monkeypatch, client)
    monkeypatch.setattr(detector, "_check_path_exists", lambda *a, **k: (False, 404))
    detector._scan_once("http://api", "tok")
    assert client.posts == []  # local skipped; fresh within grace → not deleted


def test_scan_bad_created_at_then_checks(monkeypatch):
    client = _ScanClient(
        [{"id": "w1", "name": "x", "repo_url": "https://github.com/o/r", "created_at": "not-a-date"}]
    )
    _patch(monkeypatch, client)
    monkeypatch.setattr(detector, "_check_path_exists", lambda *a, **k: (True, 200))
    detector._scan_once("http://api", "tok")  # alive → no delete
    assert client.posts == []


def test_scan_unknown_status_skips(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    client = _ScanClient([{"id": "w1", "name": "x", "repo_url": "https://github.com/o/r", "created_at": old}])
    _patch(monkeypatch, client)
    monkeypatch.setattr(detector, "_check_path_exists", lambda *a, **k: (None, 403))
    detector._scan_once("http://api", "tok")
    assert client.posts == []


def test_scan_orphan_auto_deletes(monkeypatch):
    client = _ScanClient([{"id": "w1", "name": "gone", "repo_url": "https://github.com/o/r"}])
    _patch(monkeypatch, client)
    monkeypatch.setattr(detector, "_check_path_exists", lambda *a, **k: (False, 404))
    detector._scan_once("http://api", "tok")
    assert len(client.posts) == 1 and client.posts[0][0].endswith("/w1/auto-delete")


def test_scan_orphan_delete_failure_logged(monkeypatch):
    client = _ScanClient(
        [{"id": "w1", "name": "gone", "repo_url": "https://github.com/o/r"}], delete_status=500
    )
    _patch(monkeypatch, client)
    monkeypatch.setattr(detector, "_check_path_exists", lambda *a, **k: (False, 404))
    detector._scan_once("http://api", "tok")  # error logged, no raise


def test_scan_github_token_fetch_error(monkeypatch):
    class _C(_ScanClient):
        def get(self, url, headers=None, params=None, timeout=None):
            if url.endswith("/internal/github-token"):
                raise httpx.RequestError("net", request=None)
            return super().get(url, headers=headers, params=params, timeout=timeout)

    client = _C([{"id": "w1", "name": "x", "repo_url": "https://github.com/o/r"}])
    _patch(monkeypatch, client)
    monkeypatch.setattr(detector, "_check_path_exists", lambda *a, **k: (True, 200))
    detector._scan_once("http://api", "tok")  # token fetch fails → continues unauthed


# ─── main ────────────────────────────────────────────────────────────────────


def test_main_requires_token(monkeypatch):
    monkeypatch.delenv("TERRADUCKTEL_INTERNAL_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        detector.main()


def test_main_loops_once(monkeypatch):
    monkeypatch.setenv("TERRADUCKTEL_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(detector, "_scan_once", lambda *a, **k: None)
    monkeypatch.setattr(detector.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        detector.main()


def test_main_swallows_scan_exception(monkeypatch):
    monkeypatch.setenv("TERRADUCKTEL_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(detector, "_scan_once", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(detector.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        detector.main()
