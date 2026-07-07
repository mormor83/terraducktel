"""Unit coverage for the inventory collector (httpx + boto3 mocked)."""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import detector  # noqa: E402


# ─── _arn_service ────────────────────────────────────────────────────────────


def test_arn_service():
    assert detector._arn_service("arn:aws:s3:::bucket") == "s3"
    assert detector._arn_service("arn:aws:ec2:us-east-1:123:instance/i-1") == "ec2"
    assert detector._arn_service("not-an-arn") == ""


# ─── _managed_from_tfstate ───────────────────────────────────────────────────


def test_managed_from_tfstate_classifies_codified():
    state = {
        "resources": [
            {"mode": "managed", "type": "aws_s3_bucket", "name": "b",
             "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
             "instances": [{"attributes": {"arn": "arn:aws:s3:::b", "id": "b"}}]},
            # data source → skipped
            {"mode": "data", "type": "aws_ami", "name": "x",
             "instances": [{"attributes": {"id": "ami-1"}}]},
            # nested module + index_key
            {"mode": "managed", "type": "aws_instance", "name": "web", "module": "module.app",
             "instances": [{"index_key": "a", "attributes": {"arn": "arn:aws:ec2:::i-1", "id": "i-1"}}]},
        ]
    }
    assets, managed_ids = detector._managed_from_tfstate(state, "us-east-1", "123")
    assert len(assets) == 2
    addrs = {a["address"] for a in assets}
    assert "aws_s3_bucket.b" in addrs
    assert 'module.app.aws_instance.web["a"]' in addrs
    assert all(a["iac_status"] == "codified" for a in assets)
    assert {a["provider"] for a in assets} == {"aws"}
    assert "arn:aws:s3:::b" in managed_ids and "i-1" in managed_ids


def test_managed_from_tfstate_empty():
    assets, ids = detector._managed_from_tfstate({}, "us-east-1", "123")
    assert assets == [] and ids == set()


def test_managed_from_tfstate_skips_random_password_none_id():
    """random_* resources (random_password.id == "none") are NOT emitted as
    assets — two of them would collide on the (bu, asset_id) unique key and
    500 the whole inventory report. Regression for the sample-backend-deps bug."""
    state = {
        "resources": [
            {"mode": "managed", "type": "aws_db_instance", "name": "pg",
             "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
             "instances": [{"attributes": {"arn": "arn:aws:rds:::db:pg", "id": "pg"}}]},
            {"mode": "managed", "type": "random_password", "name": "app_session",
             "provider": 'provider["registry.terraform.io/hashicorp/random"]',
             "instances": [{"attributes": {"id": "none"}}]},
            {"mode": "managed", "type": "random_password", "name": "app_admin",
             "provider": 'provider["registry.terraform.io/hashicorp/random"]',
             "instances": [{"attributes": {"id": "none"}}]},
        ]
    }
    assets, managed_ids = detector._managed_from_tfstate(state, "us-east-1", "222222222222")
    # Only the real cloud resource is codified; neither random_password leaks.
    assert [a["asset_type"] for a in assets] == ["aws_db_instance"]
    assert "none" not in managed_ids
    assert all(a["asset_id"] != "none" for a in assets)


def test_managed_from_tfstate_skips_sentinel_none_id_defensively():
    """Even a cloud-provider resource with id=='none' (shouldn't happen) is
    dropped rather than producing a colliding asset_id."""
    state = {
        "resources": [
            {"mode": "managed", "type": "aws_thing", "name": "x",
             "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
             "instances": [{"attributes": {"id": "none"}}]},
        ]
    }
    assets, _ = detector._managed_from_tfstate(state, "us-east-1", "123")
    assert assets == []


# ─── _live_resources ─────────────────────────────────────────────────────────


def test_live_resources_no_creds():
    assert detector._live_resources({}, "us-east-1") == []


def test_live_resources_skips_non_regional():
    """Non-regional workspaces (region='global'/empty) must not attempt a
    tagging-API call — there's no `tagging.global.amazonaws.com` endpoint."""
    creds = {"access_key_id": "AKIA", "secret_access_key": "s"}
    assert detector._live_resources(creds, "global") == []
    assert detector._live_resources(creds, "") == []


def test_live_resources_enumerates_with_tags(monkeypatch):
    class _Paginator:
        def paginate(self):
            yield {"ResourceTagMappingList": [
                {"ResourceARN": "arn:aws:s3:::a", "Tags": [{"Key": "Team", "Value": "x"}]},
                {"ResourceARN": "arn:aws:s3:::b", "Tags": []}]}

    class _Client:
        def get_paginator(self, _n):
            return _Paginator()

    monkeypatch.setitem(sys.modules, "boto3",
                        type("B", (), {"client": staticmethod(lambda *a, **k: _Client())}))
    res = detector._live_resources({"access_key_id": "k", "secret_access_key": "s"}, "us-east-1")
    assert res == [
        {"arn": "arn:aws:s3:::a", "tags": {"Team": "x"}},
        {"arn": "arn:aws:s3:::b", "tags": {}},
    ]


def test_live_resources_degrades_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no boto")
    monkeypatch.setitem(sys.modules, "boto3", type("B", (), {"client": staticmethod(boom)}))
    assert detector._live_resources({"access_key_id": "k", "secret_access_key": "s"}, "us-east-1") == []


# ─── _service_owner ────────────────────────────────────────────────────────--


def test_service_owner_detects_known_tags():
    assert detector._service_owner({"eks:cluster-name": "prod"}) == "EKS"
    assert detector._service_owner({"kubernetes.io/cluster/prod": "owned"}) == "EKS"  # prefix glob
    assert detector._service_owner({"aws:cloudformation:stack-id": "x"}) == "CloudFormation"
    assert detector._service_owner({"karpenter.sh/nodepool": "default"}) == "Karpenter"
    assert detector._service_owner({"aws:autoscaling:groupName": "asg"}) == "Auto Scaling"
    assert detector._service_owner({"Team": "x"}) is None
    assert detector._service_owner({}) is None


def test_service_owner_detects_current_eks_lb_controller_tags():
    """Regression: the AWS LB Controller's current tag namespace was unmatched,
    so EKS-created ALBs/listeners showed as `unmanaged` instead of
    `service_managed`. These are the exact tags off a live `demo` listener."""
    # `eks:eks-cluster-name` (newer EKS managed-tag key)
    assert detector._service_owner({"eks:eks-cluster-name": "demo"}) == "EKS"
    # AWS LB Controller listener/target-group/rule tags
    assert detector._service_owner({"ingress.eks.amazonaws.com/stack": "demo-apps"}) == "AWS LB Controller"
    assert detector._service_owner({"ingress.eks.amazonaws.com/resource": "443"}) == "AWS LB Controller"
    assert detector._service_owner({"service.eks.amazonaws.com/stack": "x"}) == "AWS LB Controller"
    # full real-world tag set (EKS wins as it's checked first — either is fine)
    real = {"ingress.eks.amazonaws.com/resource": "443", "eks:eks-cluster-name": "demo",
            "ingress.eks.amazonaws.com/stack": "demo-apps"}
    assert detector._service_owner(real) in ("EKS", "AWS LB Controller")


# ─── _analyze_workspace ──────────────────────────────────────────────────────


def test_analyze_classifies_codified_unmanaged_and_service_managed():
    state = {"resources": [
        {"mode": "managed", "type": "aws_s3_bucket", "name": "b",
         "instances": [{"attributes": {"arn": "arn:aws:s3:::b", "id": "b"}}]}]}
    live = [
        {"arn": "arn:aws:s3:::b", "tags": {}},                       # codified (in state)
        {"arn": "arn:aws:s3:::ghost", "tags": {}},                   # unmanaged
        {"arn": "arn:aws:ec2:::fleet/f1", "tags": {"eks:cluster-name": "prod"}},  # service-managed
    ]
    out = detector._analyze_workspace(
        {"name": "vpc", "region": "us-east-1"}, {"account_id": "123"}, state, live)
    assert out["has_drift"] is False
    assert out["untracked_count"] == 1  # only the genuine unmanaged one
    by_status = {a["iac_status"] for a in out["assets"]}
    assert by_status == {"codified", "unmanaged", "service_managed"}
    sm = [a for a in out["assets"] if a["iac_status"] == "service_managed"][0]
    assert sm["asset_id"] == "arn:aws:ec2:::fleet/f1" and "EKS" in sm["drift_summary"]
    assert "1 codified, 1 unmanaged, 1 service-managed" in out["summary"]


def test_analyze_empty_state_all_unmanaged():
    out = detector._analyze_workspace(
        {"name": "x", "region": "us-east-1"}, {"account_id": "1"}, {},
        [{"arn": "arn:aws:s3:::ghost", "tags": {}}])
    assert out["untracked_count"] == 1 and len(out["assets"]) == 1


# ─── httpx fake + _scan_once ─────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code=200, json_data=None, content=b"{}"):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _Client:
    """Routes GETs by URL substring (most-specific needles listed first)."""

    def __init__(self, routes, ws_resp):
        self._routes = routes
        self._ws = ws_resp
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        for needle, resp in self._routes.items():
            if needle in url:
                return resp
        if "/internal/workspaces" in url:
            return self._ws
        return _Resp(404, content=b"")

    def post(self, url, headers=None, json=None):
        self.posts.append(json)
        return _Resp(200)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(detector.httpx, "Client", lambda *a, **k: client)


def test_scan_401_returns_early(monkeypatch):
    _patch_client(monkeypatch, _Client({}, _Resp(401)))
    detector._scan_once("http://api", "internal-tok", "state-tok")  # no raise


def test_scan_non_list_payload(monkeypatch):
    _patch_client(monkeypatch, _Client({}, _Resp(200, json_data={"not": "a list"})))
    detector._scan_once("http://api", "internal-tok", "state-tok")


def test_scan_reports_codified_and_unmanaged(monkeypatch):
    ws = _Resp(200, json_data=[{"id": "w1", "name": "vpc", "region": "us-east-1", "aws_account_id": "123"}])
    routes = {
        "aws-credentials": _Resp(200, json_data={"access_key_id": "", "secret_access_key": "", "account_id": "123"}),
        "/state/": _Resp(200, json_data={"resources": [
            {"mode": "managed", "type": "aws_s3_bucket", "name": "b",
             "instances": [{"attributes": {"arn": "arn:aws:s3:::b", "id": "b"}}]}]}),
    }
    client = _Client(routes, ws)
    _patch_client(monkeypatch, client)
    detector._scan_once("http://api", "internal-tok", "state-tok")
    assert len(client.posts) == 1
    body = client.posts[0]
    assert body["workspace_id"] == "w1"
    assert body["has_drift"] is False
    assert [a["iac_status"] for a in body["assets"]] == ["codified"]  # empty creds → no live scan


def test_scan_skips_workspaces_without_id(monkeypatch):
    ws = _Resp(200, json_data=[{"name": "no-id"}, {"id": "w1", "name": "vpc"}])
    client = _Client({"aws-credentials": _Resp(200, json_data={}), "/state/": _Resp(200, json_data={})}, ws)
    _patch_client(monkeypatch, client)
    detector._scan_once("http://api", "internal-tok", "state-tok")
    assert len(client.posts) == 1 and client.posts[0]["workspace_id"] == "w1"


def test_scan_report_failure_logged(monkeypatch):
    ws = _Resp(200, json_data=[{"id": "w1", "name": "vpc"}])

    class _C(_Client):
        def post(self, url, headers=None, json=None):
            self.posts.append(json)
            return _Resp(500)

    client = _C({"aws-credentials": _Resp(200, json_data={}), "/state/": _Resp(200, json_data={})}, ws)
    _patch_client(monkeypatch, client)
    detector._scan_once("http://api", "internal-tok", "state-tok")  # warning, no raise


# ─── _fetch_state / _fetch_credentials ───────────────────────────────────────


def test_fetch_state_200_and_404(monkeypatch):
    client = _Client({"/state/": _Resp(200, json_data={"resources": []}, content=b"{}")}, _Resp(200))
    assert detector._fetch_state(client, "http://api", {}, "w1") == {"resources": []}
    empty = _Client({}, _Resp(200))
    assert detector._fetch_state(empty, "http://api", {}, "missing") == {}


# ─── main ────────────────────────────────────────────────────────────────────


def test_main_requires_internal_token(monkeypatch):
    monkeypatch.delenv("TERRADUCKTEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "tok")
    with pytest.raises(SystemExit):
        detector.main()


def test_main_requires_state_token(monkeypatch):
    monkeypatch.setenv("TERRADUCKTEL_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("TERRADUCKTEL_STATE_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        detector.main()


def test_main_loops_once_then_stops(monkeypatch):
    monkeypatch.setenv("TERRADUCKTEL_INTERNAL_TOKEN", "tok")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "tok2")
    monkeypatch.setenv("DRIFT_INTERVAL_SEC", "0")
    calls = {"scan": 0}
    monkeypatch.setattr(detector, "_scan_once", lambda *a, **k: calls.__setitem__("scan", calls["scan"] + 1))

    def fake_sleep(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(detector.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        detector.main()
    assert calls["scan"] == 1


def test_main_swallows_scan_exception(monkeypatch):
    monkeypatch.setenv("TERRADUCKTEL_INTERNAL_TOKEN", "tok")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "tok2")

    def boom(*a, **k):
        raise RuntimeError("scan failed")

    def fake_sleep(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(detector, "_scan_once", boom)
    monkeypatch.setattr(detector.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        detector.main()
