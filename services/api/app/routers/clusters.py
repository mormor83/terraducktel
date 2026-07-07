"""Kubernetes cluster CRUD with an encrypted kubeconfig at rest.

Mirrors routers/aws_accounts.py: BU-scoped list/create/update/delete plus a
`/{id}/test` connectivity probe. The kubeconfig is NEVER returned or logged —
responses carry only a masked tail. RBAC matches aws_accounts: read=viewer,
write/delete=admin.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.k8s_cluster import K8sCluster
from app.models.user import User
from app.services import cluster_service as clusters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/clusters", tags=["clusters"])


# --- Schemas (kept local to mirror the CLUSTERS REST CONTRACT exactly) -------

class ClusterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    server_url: Optional[str] = None
    default_namespace: Optional[str] = None
    # Optional AWS account (12-digit id) whose creds authenticate to EKS via
    # the kubeconfig's `aws eks get-token` exec plugin. Null for non-EKS.
    aws_account_id: Optional[str] = None
    # Full kubeconfig YAML. Encrypted at rest; never echoed back.
    kubeconfig: str = Field(..., min_length=1)


class ClusterUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    server_url: Optional[str] = None
    default_namespace: Optional[str] = None
    aws_account_id: Optional[str] = None
    # If supplied we re-encrypt on the server.
    kubeconfig: Optional[str] = None


class ClusterResponse(BaseModel):
    """Response shape — NEVER returns the kubeconfig plaintext."""
    id: str
    business_unit_id: str
    name: str
    description: Optional[str] = None
    server_url: Optional[str] = None
    default_namespace: Optional[str] = None
    aws_account_id: Optional[str] = None
    kubeconfig_tail: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": False}


class ClusterTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    context: Optional[str] = None


def _to_response(c: K8sCluster) -> ClusterResponse:
    tail = "(unreadable)"
    try:
        plain = clusters.decrypt_secret(c.kubeconfig_encrypted)
        tail = clusters.mask_tail(plain)
    except Exception:
        # Decryption failure usually means the encryption key changed — surface
        # the row but mark the config unreadable rather than 500-ing the list.
        tail = "(unreadable)"
    return ClusterResponse(
        id=c.id,
        business_unit_id=c.business_unit_id,
        name=c.name,
        description=c.description,
        server_url=c.server_url,
        default_namespace=c.default_namespace,
        aws_account_id=getattr(c, "aws_account_id", None),
        kubeconfig_tail=tail,
        created_at=getattr(c, "created_at", None),
    )


@router.get("", response_model=list[ClusterResponse])
async def list_clusters(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    rows = await clusters.list_clusters(db, business_unit_id=bu.bu_id)
    return [_to_response(c) for c in rows]


@router.post("", response_model=ClusterResponse, status_code=status.HTTP_201_CREATED)
async def create_cluster(
    body: ClusterCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    # New clusters always belong to the currently-scoped BU. Superadmin must
    # pick a concrete BU via X-Business-Unit header — refuse "all".
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a cluster",
        )
    try:
        cluster = await clusters.create_cluster(
            db,
            business_unit_id=bu.bu_id,
            name=body.name,
            description=body.description,
            server_url=body.server_url,
            default_namespace=body.default_namespace,
            aws_account_id=body.aws_account_id,
            kubeconfig=body.kubeconfig,
        )
    except clusters.UnsafeKubeconfigError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return _to_response(cluster)


@router.put("/{cluster_id}", response_model=ClusterResponse)
async def update_cluster(
    cluster_id: str,
    body: ClusterUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    cluster = await clusters.get_cluster(db, cluster_id, business_unit_id=bu.bu_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    data = body.model_dump(exclude_unset=True)
    try:
        cluster = await clusters.update_cluster(
            db,
            cluster,
            name=data.get("name"),
            description=data.get("description"),
            server_url=data.get("server_url"),
            default_namespace=data.get("default_namespace"),
            kubeconfig=data.get("kubeconfig"),
            aws_account_id=data.get("aws_account_id"),
            aws_account_id_set=("aws_account_id" in data),
        )
    except clusters.UnsafeKubeconfigError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return _to_response(cluster)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cluster(
    cluster_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    cluster = await clusters.get_cluster(db, cluster_id, business_unit_id=bu.bu_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    await clusters.delete_cluster(db, cluster)


@router.post("/{cluster_id}/test", response_model=ClusterTestResult)
async def test_cluster(
    cluster_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Validate connectivity by running `kubectl version` against the cluster.

    Writes the decrypted kubeconfig to a private temp file, runs kubectl with a
    short timeout, then removes the file. The kubeconfig is NEVER logged or
    returned — only ok/detail/context surface to the UI.
    """
    cluster = await clusters.get_cluster(db, cluster_id, business_unit_id=bu.bu_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    try:
        kubeconfig = clusters.decrypt_secret(cluster.kubeconfig_encrypted)
    except Exception:
        logger.warning("Kubeconfig decryption failed for cluster %s", cluster.id)
        return ClusterTestResult(
            ok=False,
            detail="kubeconfig could not be decrypted (encryption key changed?)",
        )

    tmp_path: str | None = None
    try:
        # 0600 temp file so the kubeconfig isn't world-readable while present.
        fd, tmp_path = tempfile.mkstemp(prefix="tdt-kubeconfig-", suffix=".yaml")
        try:
            os.write(fd, kubeconfig.encode("utf-8"))
        finally:
            os.close(fd)

        env = dict(os.environ)
        env["KUBECONFIG"] = tmp_path

        # EKS kubeconfigs auth via `aws eks get-token` (exec plugin), which needs
        # AWS credentials. If the cluster links an onboarded AWS account, export
        # its decrypted creds so the plugin can mint a token. Never logged.
        if getattr(cluster, "aws_account_id", None):
            from app.services import aws_account_service as accs

            creds = await accs.list_account_credentials(
                db, cluster.aws_account_id, business_unit_id=bu.bu_id
            )
            if creds is not None:
                env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"] = creds
                acc_row = await accs.get_account_by_account_id(
                    db, cluster.aws_account_id, business_unit_id=bu.bu_id
                )
                if acc_row is not None and (acc_row.default_region or "").strip():
                    env["AWS_DEFAULT_REGION"] = acc_row.default_region.strip()

        try:
            proc = subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    tmp_path,
                    "version",
                    "--request-timeout=8s",
                    "--output=json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
        except FileNotFoundError:
            return ClusterTestResult(
                ok=False,
                detail="kubectl is not installed on the API host",
            )
        except subprocess.TimeoutExpired:
            return ClusterTestResult(
                ok=False,
                detail="kubectl timed out connecting to the cluster",
            )

        # Resolve the active context name without leaking config contents.
        context_name: str | None = None
        try:
            ctx_proc = subprocess.run(
                ["kubectl", "--kubeconfig", tmp_path, "config", "current-context"],
                capture_output=True,
                text=True,
                timeout=8,
                env=env,
            )
            if ctx_proc.returncode == 0:
                context_name = ctx_proc.stdout.strip() or None
        except Exception:
            context_name = None

        if proc.returncode == 0:
            return ClusterTestResult(ok=True, context=context_name)

        # Surface a sanitized, truncated stderr — kubectl errors don't echo the
        # kubeconfig body, but truncate defensively anyway.
        detail = (proc.stderr or proc.stdout or "kubectl returned non-zero").strip()
        return ClusterTestResult(ok=False, detail=detail[:300], context=context_name)
    except Exception as e:  # noqa: BLE001 — bubble up sanitized error to UI
        logger.warning("Cluster connectivity test failed for cluster %s", cluster.id)
        return ClusterTestResult(ok=False, detail=str(e)[:200])
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
