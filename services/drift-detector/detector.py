"""Periodic cloud-asset inventory collector.

For each workspace the collector does NOT clone the repo or run Terraform. It
reuses what the API already holds:

  1. Fetch the workspace's Terraform state from the API's HTTP state backend
     (GET /api/v1/state/{id}) — the same state Terraform reads/writes. Managed
     resources in it are classified `codified`.
  2. Fetch the per-account AWS credentials (GET /api/v1/internal/workspaces/
     {id}/aws-credentials) and enumerate live resources via the AWS Resource
     Groups Tagging API.
  3. Diff: a live ARN absent from any state is `unmanaged` (ghost / rogue infra).
  4. POST the classified asset set to the API, which upserts the inventory.

This avoids GitHub access, `terraform init`/backend wiring, and provider
`profile=` handling entirely — the API + the stored account config are the only
inputs. Attribute-level drift (codified-but-changed) needs a real plan and is
out of scope here; this collector populates the codification / unmanaged
inventory. Unmanaged detection covers only *taggable* resources the Tagging API
returns (AWS Config is a fuller-coverage future enhancement).
"""
from __future__ import annotations

import logging
import os
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Terraform providers whose resources are logical, not cloud assets — they don't
# belong in a cloud inventory and several use non-unique sentinel ids (e.g.
# `random_password.id == "none"`). Excluded from `_managed_from_tfstate`.
NON_CLOUD_PROVIDERS = {
    "random", "null", "tls", "time", "local", "external", "archive", "template",
}


def _arn_service(arn: str) -> str:
    """Best-effort service name from an ARN (arn:aws:<service>:...)."""
    parts = arn.split(":")
    return parts[2] if len(parts) > 2 else ""


# ─── tfstate parsing (managed resources) ─────────────────────────────────────


def _managed_from_tfstate(state: dict, region: str, account_id: str) -> tuple[list, set]:
    """Parse a raw Terraform state document into codified assets.

    Returns (assets, managed_ids). `managed_ids` is the set of ARNs/ids used to
    diff against the live scan. Only `mode == "managed"` resources count; data
    sources are skipped. Handles state v4 (flat `resources[]` with a `module`
    field on nested-module resources).

    Resources from non-cloud "logical" providers (random/null/tls/time/…) are
    skipped: they aren't cloud assets, and several carry non-unique sentinel ids
    — notably `random_password`, whose `id` is the literal string ``"none"``.
    Two such resources in a BU would collide on the `(business_unit_id,
    asset_id)` unique key and 500 the whole inventory report, dropping every
    other resource in the workspace (which is then mis-reported as unmanaged).
    """
    assets: list[dict] = []
    managed_ids: set[str] = set()

    for res in state.get("resources", []) or []:
        if res.get("mode") != "managed":
            continue
        rtype = res.get("type", "")
        rname = res.get("name", "")
        module = res.get("module", "")  # e.g. "module.vpc" (absent at root)
        provider_raw = res.get("provider", "")
        provider = "aws"
        if "hashicorp/" in provider_raw:
            provider = provider_raw.split("hashicorp/")[-1].rstrip('"]')

        if provider in NON_CLOUD_PROVIDERS:
            continue

        for inst in res.get("instances", []) or []:
            attrs = inst.get("attributes") or {}
            arn = attrs.get("arn")
            rid = attrs.get("id")
            asset_id = arn or rid
            # Skip empty or non-identifying ids. `"none"` is the random
            # provider's sentinel id (and never a real cloud resource id) — a
            # belt-and-suspenders guard alongside the NON_CLOUD_PROVIDERS skip.
            if not asset_id or asset_id == "none":
                continue
            if arn:
                managed_ids.add(arn)
            if rid:
                managed_ids.add(rid)

            address = ".".join(p for p in [module, f"{rtype}.{rname}"] if p)
            idx = inst.get("index_key")
            if idx is not None:
                address += f'["{idx}"]' if isinstance(idx, str) else f"[{idx}]"

            assets.append(
                {
                    "asset_id": asset_id,
                    "address": address,
                    "asset_type": rtype,
                    "provider": provider,
                    "region": region,
                    "account_id": account_id,
                    "iac_status": "codified",
                    "drift_summary": "",
                }
            )

    return assets, managed_ids


# ─── AWS live scan ───────────────────────────────────────────────────────────


# Tag keys that mark a resource as owned/created by an AWS service rather than
# directly by Terraform. The Terraform config owns the *parent* (e.g. the EKS
# cluster); these tagged children are not "rogue", so we class them
# `service_managed`, not `unmanaged`. Matched as exact keys or `prefix*` globs.
_SERVICE_OWNER_TAGS = [
    # `eks:eks-cluster-name` is the current managed-tag key AWS stamps (the older
    # `eks:cluster-name` is kept for back-compat).
    ("EKS", ["eks:cluster-name", "eks:eks-cluster-name", "aws:eks:cluster-name",
             "kubernetes.io/cluster/*", "alpha.eksctl.io/cluster-name",
             "eks:nodegroup-name", "aws:eks:nodegroup-name"]),
    ("Karpenter", ["karpenter.sh/*", "karpenter.k8s.aws/*"]),
    ("CloudFormation", ["aws:cloudformation:stack-id", "aws:cloudformation:stack-name"]),
    ("Auto Scaling", ["aws:autoscaling:groupName"]),
    ("Elastic Beanstalk", ["elasticbeanstalk:environment-id", "elasticbeanstalk:environment-name"]),
    # The AWS Load Balancer Controller now tags ALBs/NLBs + their listeners,
    # target groups and rules under the `*.eks.amazonaws.com/*` namespace; the
    # older `*.k8s.aws/*` keys are kept for back-compat.
    ("AWS LB Controller", ["elbv2.k8s.aws/cluster", "ingress.k8s.aws/*", "service.k8s.aws/*",
                           "ingress.eks.amazonaws.com/*", "service.eks.amazonaws.com/*",
                           "elbv2.eks.amazonaws.com/*"]),
    ("ECS", ["aws:ecs:clusterName", "aws:ecs:serviceName"]),
    ("Service Catalog", ["aws:servicecatalog:provisionedProductArn"]),
]


def _service_owner(tags: dict) -> str | None:
    """Return the owning AWS service if a tag marks this resource service-owned."""
    keys = list(tags or {})
    for owner, patterns in _SERVICE_OWNER_TAGS:
        for pat in patterns:
            if pat.endswith("*"):
                pre = pat[:-1]
                if any(k.startswith(pre) for k in keys):
                    return owner
            elif pat in tags:
                return owner
    return None


def _live_resources(creds: dict, region: str) -> list:
    """Enumerate live taggable resources (ARN + tags) for an account/region.

    Returns [{"arn": str, "tags": {k: v}}]. Degrades to [] on any failure
    (missing creds, boto3 absent, API error) — logged, never fatal.
    """
    access_key = (creds or {}).get("access_key_id") or ""
    secret_key = (creds or {}).get("secret_access_key") or ""
    if not access_key or not secret_key:
        logger.info("live scan skipped — no AWS credentials")
        return []
    # Non-regional workspaces (cloudflare / azure / helm charts carry
    # region="global" or none) have no AWS regional tagging endpoint — boto3
    # would build `tagging.global.amazonaws.com`, which doesn't resolve. Skip.
    if not region or region == "global":
        logger.info("live scan skipped — non-regional workspace (region=%r)", region)
        return []
    try:
        import boto3  # lazy: keeps the import optional for unit tests

        client = boto3.client(
            "resourcegroupstaggingapi",
            region_name=region or "us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        out: list[dict] = []
        for page in client.get_paginator("get_resources").paginate():
            for item in page.get("ResourceTagMappingList", []) or []:
                arn = item.get("ResourceARN")
                if arn:
                    tags = {t.get("Key"): t.get("Value") for t in item.get("Tags", []) or []}
                    out.append({"arn": arn, "tags": tags})
        return out
    except Exception:  # noqa: BLE001 — graceful degrade
        logger.exception("live scan failed")
        return []


# ─── analysis ────────────────────────────────────────────────────────────────


def _analyze_workspace(workspace: dict, creds: dict, state: dict, live: list) -> dict:
    """Classify one workspace's assets → report payload fields.

    codified = managed resources in tfstate; the live resources (ARN+tags) not
    in state are split into `service_managed` (an ownership tag identifies an
    AWS service like EKS/CloudFormation) vs genuine `unmanaged`. The API de-dups
    these across sibling workspaces in the same account.
    """
    ws_name = workspace.get("name", workspace.get("id"))
    region = workspace.get("region", "us-east-1")
    account_id = (creds or {}).get("account_id", "") or workspace.get("aws_account_id", "")

    managed_assets, managed_ids = _managed_from_tfstate(state, region, account_id)

    unmanaged_resources: list[dict] = []
    extra_assets: list[dict] = []
    unmanaged = service_managed = 0
    for item in live:
        arn = item.get("arn") if isinstance(item, dict) else item
        if not arn or arn in managed_ids:
            continue
        owner = _service_owner(item.get("tags", {})) if isinstance(item, dict) else None
        if owner:
            service_managed += 1
            status, summary = "service_managed", f"managed by {owner}"
        else:
            unmanaged += 1
            status, summary = "unmanaged", "live resource not present in tfstate"
            unmanaged_resources.append({
                "address": arn, "type": _arn_service(arn), "provider": "aws",
                "drift_type": "untracked", "summary": summary,
            })
        extra_assets.append({
            "asset_id": arn,
            "address": "",
            "asset_type": _arn_service(arn),
            "provider": "aws",
            "region": region,
            "account_id": account_id,
            "iac_status": status,
            "drift_summary": summary,
        })

    codified = len(managed_assets)
    summary = f"{ws_name}: {codified} codified, {unmanaged} unmanaged, {service_managed} service-managed"

    return {
        # has_drift stays False — this collector does inventory, not plan-based
        # drift, so it must not flip the workspace's drift_status badge.
        "has_drift": False,
        "summary": summary,
        "plan_output": "",
        "modified_count": 0,
        "untracked_count": unmanaged,
        "deleted_count": 0,
        "mismatch_count": 0,
        "resources": unmanaged_resources,
        "assets": managed_assets + extra_assets,
    }


# ─── API helpers ───────────────────────────────────────────────────────────--


def _fetch_credentials(client: httpx.Client, base: str, headers: dict, wid: str) -> dict:
    """Fetch decrypted AWS creds for a workspace from the API. {} on failure."""
    try:
        r = client.get(
            f"{base}/api/v1/internal/workspaces/{wid}/aws-credentials", headers=headers
        )
        if r.status_code == 200:
            return r.json()
        logger.warning("credential fetch for %s returned %s", wid, r.status_code)
    except Exception:  # noqa: BLE001
        logger.exception("credential fetch failed for %s", wid)
    return {}


def _fetch_state(client: httpx.Client, base: str, headers: dict, wid: str) -> dict:
    """Fetch a workspace's raw Terraform state from the HTTP state backend.

    {} if the workspace has no state yet (never applied) or on error.
    """
    try:
        r = client.get(f"{base}/api/v1/state/{wid}", headers=headers)
        if r.status_code == 200 and r.content:
            return r.json()
        if r.status_code not in (200, 204, 404):
            logger.warning("state fetch for %s returned %s", wid, r.status_code)
    except Exception:  # noqa: BLE001
        logger.exception("state fetch failed for %s", wid)
    return {}


def _scan_once(api_url: str, internal_token: str, state_token: str) -> None:
    # Two DELIBERATELY separate tokens: the internal token guards the
    # cross-tenant /api/v1/internal/* routes (list every workspace, hand out
    # plaintext AWS creds — never given to executor containers), while the
    # state token guards only the Terraform HTTP state backend. Do not
    # collapse these back into one header dict — see app/auth/internal_token.py.
    internal_headers = {"X-Terraducktel-Internal-Token": internal_token}
    state_headers = {"X-Terraducktel-State-Token": state_token}
    base = api_url.rstrip("/")

    with httpx.Client(timeout=60.0) as client:
        r = client.get(f"{base}/api/v1/internal/workspaces", headers=internal_headers)
        if r.status_code == 401:
            logger.warning("API returned 401 — TERRADUCKTEL_INTERNAL_TOKEN must match between API and detector")
            return
        r.raise_for_status()
        workspaces = r.json()
        if not isinstance(workspaces, list):
            logger.error("unexpected workspaces payload")
            return

        # Cache the live tagging scan per (account, region) — it's account-wide,
        # so re-scanning for every workspace in the same account is wasteful.
        live_cache: dict[tuple, list] = {}

        for ws in workspaces:
            wid = ws.get("id")
            ws_name = ws.get("name", wid)
            if not wid:
                continue

            logger.info("scanning workspace %s (%s)", ws_name, wid)
            try:
                creds = _fetch_credentials(client, base, internal_headers, wid)
                region = ws.get("region", "us-east-1")
                account_id = (creds or {}).get("account_id", "") or ws.get("aws_account_id", "")
                cache_key = (account_id, region)
                if cache_key not in live_cache:
                    live_cache[cache_key] = _live_resources(creds, region)
                state = _fetch_state(client, base, state_headers, wid)
                report_fields = _analyze_workspace(ws, creds, state, live_cache[cache_key])
            except Exception:
                logger.exception("inventory scan failed for %s", ws_name)
                report_fields = {
                    "has_drift": False, "summary": "collector error", "plan_output": "",
                    "modified_count": 0, "untracked_count": 0, "deleted_count": 0,
                    "mismatch_count": 0, "resources": [], "assets": [],
                }

            report_payload = {"workspace_id": wid, **report_fields}
            s = client.post(
                f"{base}/api/v1/internal/drift/{wid}/report", headers=internal_headers, json=report_payload
            )
            if s.status_code not in (200, 201):
                logger.warning("inventory report failed for %s: %s", wid, s.text)
            else:
                logger.info("%s", report_fields["summary"])


def main() -> None:
    api_url = os.environ.get("API_URL", "http://api:8000")
    internal_token = os.environ.get("TERRADUCKTEL_INTERNAL_TOKEN", "") or os.environ.get("API_TOKEN", "")
    state_token = os.environ.get("TERRADUCKTEL_STATE_TOKEN", "")
    interval = int(os.environ.get("DRIFT_INTERVAL_SEC", "300"))

    if not internal_token:
        logger.error("TERRADUCKTEL_INTERNAL_TOKEN is required for the inventory collector")
        raise SystemExit(1)
    if not state_token:
        logger.error("TERRADUCKTEL_STATE_TOKEN is required for the inventory collector")
        raise SystemExit(1)

    logger.info("Inventory collector started (interval=%ss)", interval)
    while True:
        try:
            _scan_once(api_url, internal_token, state_token)
        except Exception:
            logger.exception("inventory scan iteration failed")
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
