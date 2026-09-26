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


# ─── _arn_id_candidates ──────────────────────────────────────────────────────


def test_arn_id_candidates_strips_resource_type_prefix():
    """The arn-less types: their tfstate id is the bare `nat-…`/`pcx-…`, so it
    must fall out of the live ARN for the diff to match."""
    assert "nat-0a1b2c3d4e5f67890" in detector._arn_id_candidates(
        "arn:aws:ec2:us-east-1:444444444444:natgateway/nat-0a1b2c3d4e5f67890")
    assert "pcx-0123456789abcdef0" in detector._arn_id_candidates(
        "arn:aws:ec2:us-east-1:444444444444:vpc-peering-connection/pcx-0123456789abcdef0")
    # `:`-separated resource type, and an id that itself contains `/`
    cands = detector._arn_id_candidates("arn:aws:logs:us-east-1:123:log-group:/app/api")
    assert "/app/api" in cands
    # no resource type at all (S3) — the whole resource part is the id
    assert detector._arn_id_candidates("arn:aws:s3:::my-bucket") == {"my-bucket"}


def test_arn_id_candidates_rejects_non_arns():
    assert detector._arn_id_candidates("nat-0a1b2c3d4e5f67890") == set()
    assert detector._arn_id_candidates("arn:aws:s3:::") == set()
    assert detector._arn_id_candidates("arn:aws:ec2:us-east-1:123") == set()


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
    assets, managed_ids, arnless_ids = detector._managed_from_tfstate(state, "us-east-1", "123")
    assert len(assets) == 2
    addrs = {a["address"] for a in assets}
    assert "aws_s3_bucket.b" in addrs
    assert 'module.app.aws_instance.web["a"]' in addrs
    assert all(a["iac_status"] == "codified" for a in assets)
    assert {a["provider"] for a in assets} == {"aws"}
    assert "arn:aws:s3:::b" in managed_ids and "i-1" in managed_ids
    assert arnless_ids == set()  # both resources carry an `arn`


def test_managed_from_tfstate_collects_arnless_ids():
    """Resources the AWS provider gives no `arn` land in `arnless_ids` so the
    live ARN can be matched by id instead."""
    state = {
        "resources": [
            {"mode": "managed", "type": "aws_nat_gateway", "name": "a",
             "instances": [{"attributes": {"id": "nat-0a1b2c3d4e5f67890"}}]},
            {"mode": "managed", "type": "aws_vpc", "name": "main",
             "instances": [{"attributes": {"arn": "arn:aws:ec2:::vpc/vpc-1", "id": "vpc-1"}}]},
        ]
    }
    _, managed_ids, arnless_ids = detector._managed_from_tfstate(state, "us-east-1", "123")
    assert arnless_ids == {"nat-0a1b2c3d4e5f67890"}
    assert "vpc-1" in managed_ids and "vpc-1" not in arnless_ids


def test_managed_from_tfstate_empty():
    assets, ids, arnless = detector._managed_from_tfstate({}, "us-east-1", "123")
    assert assets == [] and ids == set() and arnless == set()


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
    assets, managed_ids, _ = detector._managed_from_tfstate(state, "us-east-1", "222222222222")
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
    assets, _, _ = detector._managed_from_tfstate(state, "us-east-1", "123")
    assert assets == []


# ─── _live_resources ─────────────────────────────────────────────────────────


def test_live_resources_no_creds():
    assert detector._live_resources({}, "us-east-1") is None


def test_live_resources_skips_non_regional():
    """Non-regional workspaces (region='global'/empty) must not attempt a
    tagging-API call — there's no `tagging.global.amazonaws.com` endpoint."""
    creds = {"access_key_id": "AKIA", "secret_access_key": "s"}
    assert detector._live_resources(creds, "global") is None
    assert detector._live_resources(creds, "") is None


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
    assert detector._live_resources({"access_key_id": "k", "secret_access_key": "s"}, "us-east-1") is None


# ─── tag drift ───────────────────────────────────────────────────────────────


_REPO_ARN = "arn:aws:ecr:us-east-1:123:repository/app"


def _tagged_state(tags_attr: dict) -> dict:
    return {"resources": [
        {"mode": "managed", "type": "aws_ecr_repository", "name": "this", "module": "module.ecr",
         "instances": [{"attributes": {"arn": _REPO_ARN, "id": "app", **tags_attr}}]},
    ]}


def test_state_tags_prefers_tags_all_and_skips_untaggable():
    state = _tagged_state({"tags": {"A": "1"}, "tags_all": {"A": "1", "Owner": "devops"}})
    state["resources"].append({"mode": "managed", "type": "aws_iam_role_policy_attachment", "name": "x",
                               "instances": [{"attributes": {"id": "role/x", "arn": "arn:aws:iam::123:x"}}]})
    assert detector._state_tags(state) == {
        _REPO_ARN: ("module.ecr.aws_ecr_repository.this", "aws_ecr_repository", {"A": "1", "Owner": "devops"}),
    }


def test_state_tags_null_is_empty():
    assert detector._state_tags(_tagged_state({"tags_all": None}))[_REPO_ARN][2] == {}


def test_tag_diff_reports_keys_only_and_ignores_aws_prefix():
    assert detector._tag_diff({"A": "1", "B": "2"}, {"A": "1", "B": "2", "aws:cloudformation:x": "y"}) == ""
    diff = detector._tag_diff({"A": "1", "B": "2"}, {"A": "changed", "C": "secret-value"})
    assert diff == "tags changed: +C ~A -B"
    assert "secret-value" not in diff


def test_analyze_flags_tag_drift_on_managed_resource():
    state = _tagged_state({"tags_all": {"Owner": "devops"}})
    live = [{"arn": _REPO_ARN, "tags": {"Owner": "devops", "tdt-drift-test": "1"}}]
    out = detector._analyze_workspace({"name": "ecr", "region": "us-east-1"}, {"account_id": "123"}, state, live)
    assert out["has_drift"] is True and out["drift_checked"] is True
    assert out["modified_count"] == 1 and out["untracked_count"] == 0
    assert out["resources"] == [{
        "address": "module.ecr.aws_ecr_repository.this", "type": "aws_ecr_repository", "provider": "aws",
        "drift_type": "modified", "summary": "tags changed: +tdt-drift-test",
    }]
    [asset] = out["assets"]
    assert asset["iac_status"] == "drifted" and asset["drift_summary"] == "tags changed: +tdt-drift-test"
    assert "module.ecr.aws_ecr_repository.this: tags changed: +tdt-drift-test" in out["summary"]


def test_analyze_matching_tags_is_clean():
    state = _tagged_state({"tags_all": {"Owner": "devops"}})
    live = [{"arn": _REPO_ARN, "tags": {"Owner": "devops"}}]
    out = detector._analyze_workspace({"name": "ecr"}, {"account_id": "123"}, state, live)
    assert out["has_drift"] is False and out["modified_count"] == 0
    assert out["assets"][0]["iac_status"] == "codified"


def test_ec2_tag_keys_owned_by_another_workspace_are_not_drift():
    """Prod case: the EKS workspace's `aws_ec2_tag` stamps kubernetes.io/* onto
    the VPC workspace's subnets. Only the unowned key may count."""
    subnet = "arn:aws:ec2:us-east-1:123:subnet/subnet-0abc"
    vpc_state = {"resources": [
        {"mode": "managed", "type": "aws_subnet", "name": "private",
         "instances": [{"index_key": 0, "attributes": {"arn": subnet, "id": "subnet-0abc",
                                                       "tags_all": {"Name": "private"}}}]}]}
    eks_state = {"resources": [
        {"mode": "managed", "type": "aws_ec2_tag", "name": "subnet",
         "instances": [{"attributes": {"resource_id": "subnet-0abc", "key": "kubernetes.io/cluster/main"}}]}]}
    owned = detector._ec2_tag_owned(eks_state)
    assert owned == {"subnet-0abc": {"kubernetes.io/cluster/main"}}
    live = [{"arn": subnet, "tags": {"Name": "private", "kubernetes.io/cluster/main": "shared"}}]
    assert detector._analyze_workspace({"name": "vpc"}, {}, vpc_state, live, owned)["has_drift"] is False
    live[0]["tags"]["rogue"] = "x"
    out = detector._analyze_workspace({"name": "vpc"}, {}, vpc_state, live, owned)
    assert out["resources"][0]["summary"] == "tags changed: +rogue"
    assert out["resources"][0]["address"] == "aws_subnet.private[0]"


def test_analyze_without_live_scan_is_unchecked():
    """No live scan (None) must not read as "clean" — the API would flip the
    badge and re-alert on the next successful scan."""
    out = detector._analyze_workspace({"name": "ecr"}, {}, _tagged_state({"tags_all": {}}), None)
    assert out["drift_checked"] is False and out["has_drift"] is False


# ─── ghosts ──────────────────────────────────────────────────────────────────


_LT_ARN = "arn:aws:ec2:us-east-1:123:launch-template/lt-gone"


def _ghost_state() -> dict:
    return {"resources": [
        {"mode": "managed", "type": "aws_launch_template", "name": "this",
         "instances": [{"attributes": {"arn": _LT_ARN, "id": "lt-gone", "tags_all": {"Name": "t"}}}]},
        # untagged → the Tagging API wouldn't list it even if alive
        {"mode": "managed", "type": "aws_sqs_queue", "name": "q",
         "instances": [{"attributes": {"arn": "arn:aws:sqs:us-east-1:123:q", "tags_all": {}}}]},
        # only aws:-reserved tags → same
        {"mode": "managed", "type": "aws_sns_topic", "name": "t",
         "instances": [{"attributes": {"arn": "arn:aws:sns:us-east-1:123:t",
                                       "tags_all": {"aws:cloudformation:stack-name": "x"}}}]},
        # global ARN → never in a regional scan
        {"mode": "managed", "type": "aws_iam_role", "name": "r",
         "instances": [{"attributes": {"arn": "arn:aws:iam::123:role/r", "tags_all": {"A": "1"}}}]},
        # other region
        {"mode": "managed", "type": "aws_s3_bucket", "name": "b",
         "instances": [{"attributes": {"arn": "arn:aws:sqs:eu-west-1:123:far", "tags_all": {"A": "1"}}}]},
    ]}


def test_ghost_candidates_only_tagged_in_region_arns():
    st = detector._state_tags(_ghost_state())
    assert detector._ghost_candidates(st, set(), "us-east-1") == [_LT_ARN]
    assert detector._ghost_candidates(st, {_LT_ARN}, "us-east-1") == []


def test_analyze_reports_confirmed_ghost():
    seen = []

    def confirm(arns):
        seen.append(list(arns))
        return set(arns)

    out = detector._analyze_workspace({"name": "cf", "region": "us-east-1"}, {"account_id": "123"},
                                      _ghost_state(), [], confirm_ghosts=confirm)
    assert seen == [[_LT_ARN]]
    assert out["has_drift"] is True and out["deleted_count"] == 1 and out["modified_count"] == 0
    assert out["resources"][0] == {
        "address": "aws_launch_template.this", "type": "aws_launch_template", "provider": "aws",
        "drift_type": "deleted", "summary": detector.GHOST_SUMMARY,
    }
    lt = next(a for a in out["assets"] if a["asset_id"] == _LT_ARN)
    assert lt["iac_status"] == "ghost"
    assert "1 deleted (ghost)" in out["summary"]


def test_analyze_unconfirmed_ghost_is_not_reported():
    """The first scan alone is never trusted: no confirmer, or a confirmer
    that disagrees, means no ghost."""
    kw = dict(workspace={"name": "cf", "region": "us-east-1"}, creds={}, state=_ghost_state(), live=[])
    assert detector._analyze_workspace(**kw)["deleted_count"] == 0
    assert detector._analyze_workspace(**kw, confirm_ghosts=lambda arns: set())["deleted_count"] == 0


def test_analyze_no_ghosts_without_live_scan():
    called = []
    out = detector._analyze_workspace({"name": "cf", "region": "us-east-1"}, {}, _ghost_state(), None,
                                      confirm_ghosts=lambda a: called.append(a) or set(a))
    assert called == [] and out["deleted_count"] == 0 and out["drift_checked"] is False


def _fake_tagging(monkeypatch, known=(), boom=False):
    calls = []

    class _C:
        def get_resources(self, ResourceARNList):
            if boom:
                raise RuntimeError("throttled")
            calls.append(len(ResourceARNList))
            return {"ResourceTagMappingList": [{"ResourceARN": a} for a in ResourceARNList if a in known]}

    monkeypatch.setitem(sys.modules, "boto3", type("B", (), {"client": staticmethod(lambda *a, **k: _C())}))
    return calls


def test_still_live_batches_by_100(monkeypatch):
    arns = [f"arn:aws:sqs:us-east-1:123:q{i}" for i in range(150)]
    calls = _fake_tagging(monkeypatch, known={arns[3], arns[120]})
    assert detector._still_live({}, "us-east-1", arns) == {arns[3], arns[120]}
    assert calls == [100, 50]


def test_still_live_failure_confirms_nothing(monkeypatch):
    _fake_tagging(monkeypatch, boom=True)
    assert detector._still_live({}, "us-east-1", [_LT_ARN]) is None


def _ghost_scan(monkeypatch, second_state, known=()):
    """Scan one workspace whose live scan misses the launch template, with the
    state backend returning `second_state` on the re-read."""
    ws = _Resp(200, json_data=[{"id": "w1", "name": "cf", "region": "us-east-1"}])
    states = iter([_Resp(200, json_data=_ghost_state(), content=b"{}"), second_state])

    class _C(_Client):
        def get(self, url, headers=None, params=None):
            if "/state/" in url:
                return next(states)
            return super().get(url, headers, params)

    client = _C({"aws-credentials": _Resp(200, json_data={"access_key_id": "k", "secret_access_key": "s",
                                                          "account_id": "123"})}, ws)
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(detector, "_live_resources", lambda creds, region: [])
    _fake_tagging(monkeypatch, known=known)
    detector._scan_once("http://api", "internal-tok", "state-tok")
    return client.posts[0]


def test_scan_confirms_ghost_via_recheck_and_fresh_state(monkeypatch):
    body = _ghost_scan(monkeypatch, _Resp(200, json_data=_ghost_state(), content=b"{}"))
    assert body["deleted_count"] == 1 and body["has_drift"] is True


def test_scan_drops_ghost_destroyed_by_concurrent_apply(monkeypatch):
    """Resource left state between pass 1 and the re-read → it was destroyed
    on purpose, not a ghost."""
    body = _ghost_scan(monkeypatch, _Resp(200, json_data={"resources": []}, content=b"{}"))
    assert body["deleted_count"] == 0 and body["has_drift"] is False


def test_scan_drops_ghost_the_recheck_finds(monkeypatch):
    body = _ghost_scan(monkeypatch, _Resp(200, json_data=_ghost_state(), content=b"{}"), known={_LT_ARN})
    assert body["deleted_count"] == 0


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


def test_analyze_matches_arnless_types_by_id():
    """Regression: NAT gateways and VPC peering connections have no `arn`
    attribute in the AWS provider schema at all, so tfstate holds only
    `nat-…`/`pcx-…` while the tagging API returns a full ARN. They were reported
    `unmanaged` on every scan despite being fully codified."""
    acct = "444444444444"
    state = {"resources": [
        {"mode": "managed", "type": "aws_nat_gateway", "name": "a",
         "instances": [{"attributes": {"id": "nat-0a1b2c3d4e5f67890"}},
                       {"attributes": {"id": "nat-0fedcba9876543210"}}]},
        {"mode": "managed", "type": "aws_vpc_peering_connection", "name": "peer",
         "instances": [{"attributes": {"id": "pcx-0123456789abcdef0"}}]},
    ]}
    live = [
        {"arn": f"arn:aws:ec2:us-east-1:{acct}:natgateway/nat-0a1b2c3d4e5f67890", "tags": {}},
        {"arn": f"arn:aws:ec2:us-east-1:{acct}:natgateway/nat-0fedcba9876543210", "tags": {}},
        {"arn": f"arn:aws:ec2:us-east-1:{acct}:vpc-peering-connection/pcx-0123456789abcdef0", "tags": {}},
    ]
    out = detector._analyze_workspace(
        {"name": "prod", "region": "us-east-1"}, {"account_id": acct}, state, live)
    assert out["untracked_count"] == 0
    assert out["resources"] == []
    assert {a["iac_status"] for a in out["assets"]} == {"codified"}


def test_analyze_still_flags_genuinely_rogue_arnless_resource():
    """The id-based fallback must not swallow a real unmanaged resource: a NAT
    gateway absent from state stays `unmanaged`."""
    state = {"resources": [
        {"mode": "managed", "type": "aws_nat_gateway", "name": "a",
         "instances": [{"attributes": {"id": "nat-known"}}]}]}
    live = [
        {"arn": "arn:aws:ec2:us-east-1:1:natgateway/nat-known", "tags": {}},
        {"arn": "arn:aws:ec2:us-east-1:1:natgateway/nat-rogue", "tags": {}},
    ]
    out = detector._analyze_workspace(
        {"name": "x", "region": "us-east-1"}, {"account_id": "1"}, state, live)
    assert out["untracked_count"] == 1
    assert out["resources"][0]["address"] == "arn:aws:ec2:us-east-1:1:natgateway/nat-rogue"


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
    assert body["drift_checked"] is False  # empty creds → no live scan → drift unknown
    assert [a["iac_status"] for a in body["assets"]] == ["codified"]


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


def test_fetch_state_error_is_none_not_empty():
    """A backend error must not look like "no state" — that would read as no
    drift and flip a drifted workspace back to clean."""
    client = _Client({"/state/": _Resp(502)}, _Resp(200))
    assert detector._fetch_state(client, "http://api", {}, "w1") is None


def test_scan_state_error_marks_drift_unchecked(monkeypatch):
    ws = _Resp(200, json_data=[{"id": "w1", "name": "vpc", "region": "us-east-1"}])
    routes = {"aws-credentials": _Resp(200, json_data={}), "/state/": _Resp(502)}
    client = _Client(routes, ws)
    _patch_client(monkeypatch, client)
    detector._scan_once("http://api", "internal-tok", "state-tok")
    assert client.posts[0]["drift_checked"] is False


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
