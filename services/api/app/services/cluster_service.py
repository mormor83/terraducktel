"""CRUD + encryption helpers for K8sCluster (Helm/Kubernetes target clusters).

Mirrors aws_account_service: the kubeconfig is encrypted at rest with the same
Fernet/HKDF scheme keyed on CREDENTIAL_ENCRYPTION_KEY. We REUSE
aws_account_service.encrypt_secret / decrypt_secret directly so there is a
single encryption path for all stored credentials.

Plaintext kubeconfig NEVER hits an API response — only a masked tail
(`mask_tail`) is surfaced for UI display. The decrypted kubeconfig is only ever
returned to the executor launcher via `get_cluster_kubeconfig`.
"""
from __future__ import annotations

import os
from typing import Optional

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.k8s_cluster import K8sCluster
# Reuse the single credential-encryption path (Fernet/HKDF over
# CREDENTIAL_ENCRYPTION_KEY). Do NOT re-implement the Fernet scheme here.
from app.services.aws_account_service import decrypt_secret, encrypt_secret

__all__ = [
    "encrypt_secret",
    "decrypt_secret",
    "mask_tail",
    "validate_kubeconfig",
    "list_clusters",
    "get_cluster",
    "create_cluster",
    "update_cluster",
    "delete_cluster",
    "get_cluster_kubeconfig",
]


class UnsafeKubeconfigError(ValueError):
    """Raised when a kubeconfig declares a disallowed exec credential plugin."""


# kubectl/helm honor `users[].user.exec` credential plugins by RUNNING the named
# command to mint a token. A kubeconfig is attacker-influenced input
# (an admin pastes it), so an arbitrary `exec.command` is remote code execution
# on the API host / executor. We allow only the well-known cloud auth helpers
# that EKS/GKE/AKS actually need, matched by basename, and reject everything
# else (shells, interpreters, absolute paths to arbitrary binaries).
_ALLOWED_EXEC_COMMANDS = {
    "aws",
    "aws-iam-authenticator",
    "gke-gcloud-auth-plugin",
    "gcloud",
    "kubelogin",
    "az",
    "doctl",
    "eksctl",
}


def validate_kubeconfig(kubeconfig: str) -> str:
    """Reject kubeconfigs whose exec credential plugin isn't a known cloud
    auth helper. Returns the kubeconfig unchanged when safe; raises
    UnsafeKubeconfigError otherwise. Call before persisting.
    """
    try:
        docs = list(yaml.safe_load_all(kubeconfig))
    except yaml.YAMLError as e:
        raise UnsafeKubeconfigError(f"kubeconfig is not valid YAML: {e}") from e
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for user in doc.get("users") or []:
            exec_cfg = ((user or {}).get("user") or {}).get("exec")
            if not exec_cfg:
                continue
            command = (exec_cfg or {}).get("command")
            if not command or not isinstance(command, str):
                raise UnsafeKubeconfigError("kubeconfig exec plugin has no valid command")
            cmd = command.strip()
            # Reject any path-qualified command — an absolute (`/tmp/x/aws`) or
            # relative (`./aws`) path would let a basename-only allowlist be
            # bypassed by a planted binary (e.g. a repo-committed `aws` in the
            # executor's cloned workspace). The command must be a bare allowed
            # basename resolved from the trusted container PATH.
            if cmd != os.path.basename(cmd) or "/" in cmd or "\\" in cmd:
                raise UnsafeKubeconfigError(
                    f"kubeconfig exec command '{command}' must be a bare binary "
                    f"name, not a path"
                )
            if cmd not in _ALLOWED_EXEC_COMMANDS:
                raise UnsafeKubeconfigError(
                    f"kubeconfig exec credential plugin '{command}' is not an "
                    f"allowed cloud auth helper; permitted: "
                    f"{sorted(_ALLOWED_EXEC_COMMANDS)}"
                )
    return kubeconfig


def mask_tail(plain: str, n: int = 6) -> str:
    """Return only the last `n` chars of the kubeconfig for UI display.

    Mirrors aws_account_service.mask_access_key_tail — never leaks the body.
    """
    if not plain:
        return ""
    if len(plain) > n:
        return f"…{plain[-n:]}"
    return "***"


async def list_clusters(
    session: AsyncSession, business_unit_id: str | None = None
) -> list[K8sCluster]:
    """List K8s clusters, optionally filtered to one Business Unit.

    `business_unit_id=None` returns every cluster (superadmin "all BUs" view).
    Anything else applies a WHERE clause on the FK.
    """
    stmt = select(K8sCluster).order_by(K8sCluster.name)
    if business_unit_id is not None:
        stmt = stmt.where(K8sCluster.business_unit_id == business_unit_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_cluster(
    session: AsyncSession,
    cluster_id: str,
    business_unit_id: str | None = None,
) -> Optional[K8sCluster]:
    """Fetch a single cluster by primary key, optionally scoped to a BU.

    Passing `business_unit_id` enforces tenant isolation — a row from another
    BU is treated as not-found rather than returned.
    """
    stmt = select(K8sCluster).where(K8sCluster.id == cluster_id)
    if business_unit_id is not None:
        stmt = stmt.where(K8sCluster.business_unit_id == business_unit_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def create_cluster(
    session: AsyncSession,
    *,
    business_unit_id: str,
    name: str,
    kubeconfig: str,
    description: str | None = None,
    server_url: str | None = None,
    default_namespace: str | None = None,
    aws_account_id: str | None = None,
) -> K8sCluster:
    """Create and persist a new cluster, encrypting the kubeconfig at rest."""
    validate_kubeconfig(kubeconfig)
    cluster = K8sCluster(
        business_unit_id=business_unit_id,
        name=name,
        description=description,
        server_url=server_url,
        default_namespace=default_namespace,
        aws_account_id=(aws_account_id or None),
        kubeconfig_encrypted=encrypt_secret(kubeconfig),
    )
    session.add(cluster)
    await session.commit()
    await session.refresh(cluster)
    return cluster


async def update_cluster(
    session: AsyncSession,
    cluster: K8sCluster,
    *,
    name: str | None = None,
    description: str | None = None,
    server_url: str | None = None,
    default_namespace: str | None = None,
    kubeconfig: str | None = None,
    aws_account_id: str | None = None,
    aws_account_id_set: bool = False,
) -> K8sCluster:
    """Apply partial updates. Re-encrypts the kubeconfig only when supplied.

    `aws_account_id_set` distinguishes "caller didn't touch it" from "caller
    cleared it to null" (since None is the cleared value).
    """
    if name is not None:
        cluster.name = name
    if description is not None:
        cluster.description = description
    if server_url is not None:
        cluster.server_url = server_url
    if default_namespace is not None:
        cluster.default_namespace = default_namespace
    if aws_account_id_set:
        cluster.aws_account_id = (aws_account_id or None)
    if kubeconfig is not None:
        validate_kubeconfig(kubeconfig)
        cluster.kubeconfig_encrypted = encrypt_secret(kubeconfig)
    await session.commit()
    await session.refresh(cluster)
    return cluster


async def delete_cluster(session: AsyncSession, cluster: K8sCluster) -> None:
    """Delete a cluster row."""
    await session.delete(cluster)
    await session.commit()


async def get_cluster_kubeconfig(
    session: AsyncSession, cluster_id: str
) -> str | None:
    """Return the decrypted kubeconfig for the executor launcher.

    Run-time only — NOT exposed via any API endpoint. Returns None when the
    cluster does not exist. Not BU-scoped because the launcher already resolved
    the cluster from the (BU-scoped) workspace.
    """
    cluster = await session.get(K8sCluster, cluster_id)
    if cluster is None:
        return None
    return decrypt_secret(cluster.kubeconfig_encrypted)
