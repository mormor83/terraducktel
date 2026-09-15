# Proxmox Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Proxmox VE as a fourth cloud-provider slice so Terraform workspaces can target a Proxmox cluster through TDT's gated pipeline, using either the `bpg/proxmox` or `Telmate/proxmox` Terraform provider.

**Architecture:** A new per-BU `proxmox_clusters` table holds an encrypted API token (plus optional SSH key, TLS flag and CA bundle). Workspaces link to a cluster via a nullable FK, auto-linked on bulk import from the repo path `proxmox/cluster-<slug>/<node>/<stack>`. The executor service injects a canonical `TDT_PROXMOX_*` env set; the entrypoint fans it out to both providers' env-var vocabularies. The UI gets a Proxmox sub-tab in Cloud Providers plus a top-level tree group, mirroring GCP everywhere.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + Pydantic v2 + httpx (API); bash entrypoint on `hashicorp/terraform:1.10` (Alpine) (executor); React + Vite + Tailwind + vitest (UI); pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-12-proxmox-provider-design.md`

## Global Constraints

- Migrations are forward-only. New revision id `044_proxmox_clusters`, `down_revision = "043_workspace_tags"`. Never edit a merged revision.
- Secrets never leave the API: no response may include the token secret or SSH private key. Responses expose `token_secret_masked_tail` and `has_ssh_key` only.
- HKDF salt for this domain is exactly `b"terraducktel-proxmox-credentials-v1"`.
- Router prefix is `/api/v1/proxmox-clusters` (the UI's `api` client prepends `/api`, so UI calls use `/v1/proxmox-clusters`).
- Every write endpoint is gated by `require_role(Role.admin)`; list is `Role.viewer`. All are BU-scoped via `current_bu`.
- New `workspaces` rows must carry `business_unit_id`; Proxmox workspaces use `aws_account_id="global"`, `region="global"`, `state_backend="s3"`.
- No new env vars for the API. No `.env` files. No `sky-*` Tailwind colours in new UI code; use `brand-*` / `accent-*` or existing semantic tokens.
- Env-var names (verify against provider docs during Task 6, adjust only the entrypoint if they differ): bpg → `PROXMOX_VE_ENDPOINT`, `PROXMOX_VE_API_TOKEN`, `PROXMOX_VE_INSECURE`, `PROXMOX_VE_SSH_USERNAME`, `PROXMOX_VE_SSH_PRIVATE_KEY`; Telmate → `PM_API_URL`, `PM_API_TOKEN_ID`, `PM_API_TOKEN_SECRET`, `PM_TLS_INSECURE`.
- Commit messages: conventional commits, end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Backend tests: `cd services/api && python -m pytest tests/<file> -v`. Frontend unit tests: `cd services/ui && npx vitest run <file>`.

## Spec deviations (decided while reading the code, spec updated in Task 11)

1. **`region` stays `"global"`** for Proxmox workspaces. Discovery already stamps `global/global` on every non-AWS path, and the UI derives the Azure region / GCP region from the path's third segment. Proxmox follows that: the node name lives in the path and the UI reads it from there. No change to `repo_discovery.py`.
2. **Only bulk import auto-links.** `repo_sync.py` never creates workspaces, so there is no "repo-sync auto-link" to add. The spec sentence saying otherwise is corrected in Task 11.
3. **`account_colors.used_colors_for_bu` does need a change.** It enumerates every provider table so colours are unique BU-wide; `ProxmoxCluster` must be added to that tuple.

## File structure

| File | Responsibility |
|---|---|
| `services/api/alembic/versions/044_proxmox_clusters.py` | Create table + workspace FK |
| `services/api/app/models/proxmox_cluster.py` | ORM model |
| `services/api/app/services/proxmox_cluster_service.py` | Fernet context, CRUD helpers, `ProxmoxCredentials`, `probe_version()` |
| `services/api/app/schemas/proxmox_cluster.py` | Pydantic Create/Update/Response/TestResult |
| `services/api/app/routers/proxmox_clusters.py` | HTTP surface |
| `services/api/app/models/workspace.py`, `schemas/workspace.py`, `routers/workspaces.py` | Link column, validation, path auto-link |
| `services/api/app/services/account_colors.py`, `notification_service.py` | Provider enumeration branches |
| `services/api/app/services/executor_service.py` | `TDT_PROXMOX_*` injection |
| `services/executor/entrypoint.sh` | Fan-out to both providers + CA bundle |
| `services/ui/src/pages/ProxmoxClusters.tsx` | Settings page |
| `services/ui/src/components/workspace-tree/{types,paths,icons,groups}.ts(x)`, `WorkspaceTree.tsx`, `WorkspaceLeafRow.tsx`, `AccountTag.tsx`, `accountColors.ts`, `hooks/useAccountColors.ts`, `pages/{Dashboard,Runs,RunDetail,CloudProviders}.tsx` | Provider branches |
| `docs/ARCHITECTURE.md`, `docs/API.md`, `CLAUDE.md`, spec | Docs |

---

### Task 1: Model, migration, and encryption service

**Files:**
- Create: `services/api/alembic/versions/044_proxmox_clusters.py`
- Create: `services/api/app/models/proxmox_cluster.py`
- Create: `services/api/app/services/proxmox_cluster_service.py`
- Modify: `services/api/tests/conftest.py:56` (add model import so `create_all` sees the table)
- Modify: `services/api/alembic/env.py` (add model import next to the GCP one)
- Test: `services/api/tests/test_proxmox_cluster_service.py`

**Interfaces:**
- Produces: `ProxmoxCluster` ORM model; `proxmox_cluster_service.encrypt_secret(str) -> str`, `decrypt_secret(str) -> str`, `list_clusters(session, business_unit_id=None) -> list[ProxmoxCluster]`, `get_cluster(session, pk) -> ProxmoxCluster | None`, `ProxmoxCredentials` dataclass, `get_cluster_credentials(session, pk) -> ProxmoxCredentials | None`, `mask_tail(str) -> str`, `normalize_endpoint(str) -> str`.

- [ ] **Step 1: Write the failing service tests**

```python
# services/api/tests/test_proxmox_cluster_service.py
"""Service-layer coverage for proxmox_cluster_service: crypto context is
distinct from the other provider domains, endpoint normalisation, masking."""
import pytest

from app.services import gcp_project_service as gcpsvc
from app.services import proxmox_cluster_service as svc


def test_encrypt_decrypt_round_trip():
    assert svc.decrypt_secret(svc.encrypt_secret("s3cr3t")) == "s3cr3t"


def test_ciphertext_not_interchangeable_with_gcp_domain():
    # Same root key, different HKDF salt → the GCP context must reject it.
    token = svc.encrypt_secret("s3cr3t")
    with pytest.raises(RuntimeError):
        gcpsvc.decrypt_secret(token)


def test_decrypt_garbage_raises_runtime_error():
    with pytest.raises(RuntimeError):
        svc.decrypt_secret("not-a-fernet-token")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://pve.local:8006", "https://pve.local:8006"),
        ("https://pve.local:8006/", "https://pve.local:8006"),
        ("https://pve.local:8006/api2/json", "https://pve.local:8006"),
        ("https://pve.local:8006/api2/json/", "https://pve.local:8006"),
    ],
)
def test_normalize_endpoint(raw, expected):
    assert svc.normalize_endpoint(raw) == expected


def test_mask_tail():
    assert svc.mask_tail("abcdef12-3456") == "…3456"
    assert svc.mask_tail("ab") == "…"


async def test_get_cluster_credentials(db_session):
    from app.models.business_unit import DEFAULT_BU_ID, BusinessUnit
    from app.models.proxmox_cluster import ProxmoxCluster

    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    row = ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="home", name="Home",
        endpoint="https://pve.local:8006", api_token_id="tdt@pve!ci",
        api_token_secret_encrypted=svc.encrypt_secret("sek"),
        ssh_username="root", ssh_private_key_encrypted=svc.encrypt_secret("KEY"),
        tls_insecure=True, ca_cert_pem=None,
    )
    db_session.add(row)
    await db_session.commit()

    creds = await svc.get_cluster_credentials(db_session, row.id)
    assert creds is not None
    assert creds.endpoint == "https://pve.local:8006"
    assert creds.token_id == "tdt@pve!ci" and creds.token_secret == "sek"
    assert creds.ssh_username == "root" and creds.ssh_private_key == "KEY"
    assert creds.tls_insecure is True and creds.ca_cert_pem is None
    assert await svc.get_cluster_credentials(db_session, "nope") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_proxmox_cluster_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.proxmox_cluster_service'`

- [ ] **Step 3: Write the model**

```python
# services/api/app/models/proxmox_cluster.py
"""ProxmoxCluster model — one row per Proxmox VE cluster (or standalone node)
onboarded to TDT.

Mirrors GcpProject / AzureSubscription / AwsAccount: same encryption scheme
(a distinct HKDF salt so a Proxmox token can't be confused with another
provider's secret), same per-BU uniqueness story, same "credentials never
leave the API" rule.

Proxmox has no globally unique cluster identifier (a standalone node has no
cluster name at all), so `slug` is the operator-chosen natural key. It is
what the repo path convention `proxmox/cluster-<slug>/<node>/<stack>` encodes.

Both Terraform providers (`bpg/proxmox`, `Telmate/proxmox`) are driven from
this one credential: the executor exports each provider's env-var vocabulary
from the same endpoint + API token. No object store exists on Proxmox, so
workspaces linked here always keep `state_backend=s3`.
"""
import uuid

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProxmoxCluster(Base):
    __tablename__ = "proxmox_clusters"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "slug", name="uq_proxmox_clusters_bu_slug"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    business_unit_id: Mapped[str] = mapped_column(String, nullable=False)
    # Operator-chosen natural key within a BU; appears in repo paths.
    slug: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional UI colour token (see app.services.account_colors). NULL = auto.
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # `https://host:8006` — scheme+host+port only; normalised on write.
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    # `user@realm!tokenid`. An identifier, not a secret — shown in the UI.
    api_token_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted token secret. NEVER logged, NEVER returned in responses.
    api_token_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional SSH identity for bpg resources that upload files to a node.
    ssh_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Skip TLS verification (self-signed homelab certs). Default off.
    tls_insecure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional PEM CA bundle; public material, stored plain.
    ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Write the migration**

```python
# services/api/alembic/versions/044_proxmox_clusters.py
"""proxmox_clusters table + workspaces.proxmox_cluster_id FK.

Proxmox VE as a first-class provider, mirroring gcp_projects (038) and the
workspace FK from 039. One revision because the FK is meaningless without
the table and vice versa.

Revision ID: 044_proxmox_clusters
Revises: 043_workspace_tags
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "044_proxmox_clusters"
down_revision = "043_workspace_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxmox_clusters",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("api_token_id", sa.String(length=255), nullable=False),
        sa.Column("api_token_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("ssh_username", sa.String(length=64), nullable=True),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column("tls_insecure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ca_cert_pem", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_unit_id", "slug", name="uq_proxmox_clusters_bu_slug"),
    )
    op.add_column("workspaces", sa.Column("proxmox_cluster_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_workspaces_proxmox_cluster",
        "workspaces",
        "proxmox_clusters",
        ["proxmox_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workspaces_proxmox_cluster", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "proxmox_cluster_id")
    op.drop_table("proxmox_clusters")
```

- [ ] **Step 5: Write the service**

```python
# services/api/app/services/proxmox_cluster_service.py
"""Service layer for ProxmoxCluster.

Same Fernet/HKDF crypto context as the other provider services, with its own
salt so a Proxmox token secret can't be replayed as an AWS/Azure/GCP secret.
Also owns endpoint normalisation and the live connection probe used by the
router's /test endpoint (kept here so tests can monkeypatch one function).
"""
from __future__ import annotations

import base64
import ssl
from dataclasses import dataclass
from typing import Optional

import httpx
from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.proxmox_cluster import ProxmoxCluster

_API_SUFFIX = "/api2/json"
PROBE_TIMEOUT_S = 10.0


def _fernet() -> Fernet:
    key = get_credential_encryption_key()
    if len(key) < 16:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes")
    try:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"terraducktel-proxmox-credentials-v1",
            info=b"fernet-key",
        ).derive(key)
    except InvalidKey as e:  # pragma: no cover
        raise RuntimeError("HKDF derivation failed for Proxmox credentials") from e
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Proxmox credential decryption failed") from e


def normalize_endpoint(raw: str) -> str:
    """Strip trailing slashes and any `/api2/json` suffix so we store the bare
    origin. bpg wants the origin; Telmate wants origin + /api2/json — the
    executor derives the latter."""
    v = (raw or "").strip().rstrip("/")
    if v.endswith(_API_SUFFIX):
        v = v[: -len(_API_SUFFIX)].rstrip("/")
    return v


def mask_tail(value: str) -> str:
    """Last 4 chars, ellipsis-prefixed — enough to tell two tokens apart."""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


@dataclass(frozen=True)
class ProxmoxCredentials:
    endpoint: str
    token_id: str
    token_secret: str
    tls_insecure: bool
    ssh_username: Optional[str]
    ssh_private_key: Optional[str]
    ca_cert_pem: Optional[str]


async def list_clusters(
    session: AsyncSession, business_unit_id: Optional[str] = None
) -> list[ProxmoxCluster]:
    stmt = select(ProxmoxCluster).order_by(ProxmoxCluster.name)
    if business_unit_id is not None:
        stmt = stmt.where(ProxmoxCluster.business_unit_id == business_unit_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_cluster(session: AsyncSession, cluster_pk: str) -> Optional[ProxmoxCluster]:
    return await session.get(ProxmoxCluster, cluster_pk)


async def get_cluster_credentials(
    session: AsyncSession, cluster_pk: str
) -> ProxmoxCredentials | None:
    """Plaintext credentials for the executor service. Run-time only."""
    row = await get_cluster(session, cluster_pk)
    if row is None:
        return None
    return ProxmoxCredentials(
        endpoint=row.endpoint,
        token_id=row.api_token_id,
        token_secret=decrypt_secret(row.api_token_secret_encrypted),
        tls_insecure=bool(row.tls_insecure),
        ssh_username=row.ssh_username or None,
        ssh_private_key=(
            decrypt_secret(row.ssh_private_key_encrypted)
            if row.ssh_private_key_encrypted
            else None
        ),
        ca_cert_pem=row.ca_cert_pem or None,
    )


def _verify_for(creds: ProxmoxCredentials) -> bool | ssl.SSLContext:
    if creds.tls_insecure:
        return False
    if creds.ca_cert_pem:
        return ssl.create_default_context(cadata=creds.ca_cert_pem)
    return True


async def probe_version(creds: ProxmoxCredentials) -> str:
    """GET /api2/json/version with the API token. Returns the version string.
    Raises httpx errors / RuntimeError on failure — the router turns those
    into `ok=false` results."""
    headers = {"Authorization": f"PVEAPIToken={creds.token_id}={creds.token_secret}"}
    async with httpx.AsyncClient(
        verify=_verify_for(creds), timeout=PROBE_TIMEOUT_S, follow_redirects=False
    ) as client:
        r = await client.get(f"{creds.endpoint}{_API_SUFFIX}/version", headers=headers)
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized — check token id / secret and its privileges")
    r.raise_for_status()
    data = r.json().get("data") or {}
    version = data.get("version") or "unknown"
    release = data.get("release")
    return f"{version}-{release}" if release else version
```

- [ ] **Step 6: Register the model for tests and Alembic**

In `services/api/tests/conftest.py`, after the line `import app.models.gcp_project  # noqa: F401`, add:

```python
    import app.models.proxmox_cluster  # noqa: F401
```

In `services/api/alembic/env.py`, next to the other `import app.models.*` lines, add:

```python
import app.models.proxmox_cluster  # noqa: F401
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_proxmox_cluster_service.py -v`
Expected: 9 passed (4 parametrised endpoint cases + 5 others)

- [ ] **Step 8: Commit**

```bash
git add services/api/alembic/versions/044_proxmox_clusters.py services/api/app/models/proxmox_cluster.py services/api/app/services/proxmox_cluster_service.py services/api/tests/test_proxmox_cluster_service.py services/api/tests/conftest.py services/api/alembic/env.py
git commit -m "feat(api): add proxmox_clusters model, migration and credential service

Proxmox VE joins AWS/Azure/GCP as a per-BU provider slice. Own HKDF salt so
a Proxmox token can't be confused with another provider's secret. slug is
the operator-chosen natural key because Proxmox has no global cluster id.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Schemas and router

**Files:**
- Create: `services/api/app/schemas/proxmox_cluster.py`
- Create: `services/api/app/routers/proxmox_clusters.py`
- Modify: `services/api/app/main.py:26` (import) and `:199` (include_router)
- Test: `services/api/tests/test_proxmox_clusters_router.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces: `GET/POST /api/v1/proxmox-clusters`, `PUT/DELETE /api/v1/proxmox-clusters/{pk}`, `POST /api/v1/proxmox-clusters/{pk}/test`. Response JSON keys: `id, business_unit_id, slug, name, description, color, color_effective, endpoint, api_token_id, token_secret_masked_tail, ssh_username, has_ssh_key, tls_insecure, ca_cert_pem`.

- [ ] **Step 1: Write the failing router tests**

```python
# services/api/tests/test_proxmox_clusters_router.py
"""Router coverage for /api/v1/proxmox-clusters: CRUD + BU scope + RBAC +
validation + secret redaction + /test (probe monkeypatched — no network)."""
import pytest

from app.services import proxmox_cluster_service as svc

pytestmark = pytest.mark.usefixtures("default_bu")

_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----\n"


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


def _body(slug="home", **over):
    b = {
        "slug": slug,
        "name": f"pve-{slug}",
        "endpoint": "https://pve.local:8006/",
        "api_token_id": "tdt@pve!ci",
        "api_token_secret": "11111111-2222-3333-4444-555555555555",
    }
    b.update(over)
    return b


async def _create(client, token, **over):
    r = await client.post("/api/v1/proxmox-clusters", json=_body(**over), headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


async def test_crud_and_list_redacts_secrets(auth_client, admin_token):
    row = await _create(auth_client, admin_token, ssh_username="root", ssh_private_key=_KEY)
    assert row["endpoint"] == "https://pve.local:8006"  # normalised
    assert row["api_token_id"] == "tdt@pve!ci"
    assert row["token_secret_masked_tail"] == "…5555"
    assert row["has_ssh_key"] is True
    assert row["tls_insecure"] is False
    for forbidden in ("api_token_secret", "api_token_secret_encrypted",
                      "ssh_private_key", "ssh_private_key_encrypted"):
        assert forbidden not in row
    lst = await auth_client.get("/api/v1/proxmox-clusters", headers=_h(admin_token))
    assert any(c["id"] == row["id"] for c in lst.json())
    upd = await auth_client.put(
        f"/api/v1/proxmox-clusters/{row['id']}",
        json={"name": "renamed", "tls_insecure": True, "ca_cert_pem": _PEM},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "renamed"
    assert upd.json()["tls_insecure"] is True
    assert upd.json()["ca_cert_pem"] == _PEM
    assert upd.json()["token_secret_masked_tail"] == "…5555"  # unchanged
    d = await auth_client.delete(f"/api/v1/proxmox-clusters/{row['id']}", headers=_h(admin_token))
    assert d.status_code == 204


async def test_update_rotates_secret_and_clears_ssh(auth_client, admin_token):
    row = await _create(auth_client, admin_token, slug="rot", ssh_username="root", ssh_private_key=_KEY)
    upd = await auth_client.put(
        f"/api/v1/proxmox-clusters/{row['id']}",
        json={"api_token_secret": "new-secret-9999", "ssh_username": "", "ssh_private_key": ""},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["token_secret_masked_tail"] == "…9999"
    assert upd.json()["ssh_username"] is None
    assert upd.json()["has_ssh_key"] is False


async def test_duplicate_slug_409_and_404s(auth_client, admin_token):
    await _create(auth_client, admin_token, slug="dup")
    dup = await auth_client.post("/api/v1/proxmox-clusters", json=_body(slug="dup"), headers=_h(admin_token))
    assert dup.status_code == 409
    assert (await auth_client.put("/api/v1/proxmox-clusters/x", json={"name": "n"}, headers=_h(admin_token))).status_code == 404
    assert (await auth_client.delete("/api/v1/proxmox-clusters/x", headers=_h(admin_token))).status_code == 404


@pytest.mark.parametrize(
    "over",
    [
        {"slug": "Bad_Slug"},
        {"slug": "a"},
        {"endpoint": "http://pve.local:8006"},
        {"api_token_id": "no-bang-here"},
        {"api_token_id": "tdt@pve!ci=secret-leaked"},
        {"ca_cert_pem": "not a pem"},
        {"ssh_private_key": _KEY},  # key without username
    ],
)
async def test_validation_422(auth_client, admin_token, over):
    r = await auth_client.post("/api/v1/proxmox-clusters", json=_body(**over), headers=_h(admin_token))
    assert r.status_code == 422, r.text


async def test_rbac_viewer_cannot_create_but_can_list(auth_client, viewer_token, admin_token):
    await _create(auth_client, admin_token, slug="rbac")
    r = await auth_client.post("/api/v1/proxmox-clusters", json=_body(slug="v"), headers=_h(viewer_token))
    assert r.status_code == 403
    r = await auth_client.get("/api/v1/proxmox-clusters", headers=_h(viewer_token))
    assert r.status_code == 200


async def test_create_requires_concrete_bu(auth_client, admin_token, _setup_db):
    from app.models.user import User
    from sqlalchemy import select

    async with _setup_db() as s:
        u = (await s.execute(select(User).where(User.email == "admin@test.com"))).scalars().first()
        u.is_superadmin = True
        await s.commit()
    r = await auth_client.post("/api/v1/proxmox-clusters", json=_body(), headers=_h(admin_token, bu="all"))
    assert r.status_code == 400


async def test_bu_isolation(auth_client, admin_token, _setup_db):
    """A cluster in another BU is invisible and un-updatable from `default`."""
    from app.models.business_unit import BusinessUnit
    from app.models.proxmox_cluster import ProxmoxCluster

    async with _setup_db() as s:
        s.add(BusinessUnit(id="bu-other", slug="other", name="Other"))
        s.add(ProxmoxCluster(
            id="pmx-other", business_unit_id="bu-other", slug="o", name="o",
            endpoint="https://o:8006", api_token_id="u@pam!t",
            api_token_secret_encrypted=svc.encrypt_secret("x"),
        ))
        await s.commit()
    lst = await auth_client.get("/api/v1/proxmox-clusters", headers=_h(admin_token))
    assert all(c["id"] != "pmx-other" for c in lst.json())
    assert (await auth_client.put("/api/v1/proxmox-clusters/pmx-other", json={"name": "n"}, headers=_h(admin_token))).status_code == 404
    assert (await auth_client.delete("/api/v1/proxmox-clusters/pmx-other", headers=_h(admin_token))).status_code == 404


async def test_test_endpoint_ok(auth_client, admin_token, monkeypatch):
    seen = {}

    async def fake_probe(creds):
        seen["creds"] = creds
        return "8.2.4"

    monkeypatch.setattr(svc, "probe_version", fake_probe)
    row = await _create(auth_client, admin_token, slug="ok", tls_insecure=True)
    r = await auth_client.post(f"/api/v1/proxmox-clusters/{row['id']}/test", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "detail": "Connected — Proxmox VE 8.2.4", "version": "8.2.4"}
    assert seen["creds"].token_secret == "11111111-2222-3333-4444-555555555555"
    assert seen["creds"].tls_insecure is True


async def test_test_endpoint_failure_is_ok_false(auth_client, admin_token, monkeypatch):
    async def fake_probe(creds):
        raise RuntimeError("401 Unauthorized — check token id / secret and its privileges")

    monkeypatch.setattr(svc, "probe_version", fake_probe)
    row = await _create(auth_client, admin_token, slug="bad")
    r = await auth_client.post(f"/api/v1/proxmox-clusters/{row['id']}/test", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "401" in r.json()["detail"]
    assert r.json()["version"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_proxmox_clusters_router.py -v`
Expected: every test FAILS with 404 (route not registered) or assertion on status.

- [ ] **Step 3: Write the schemas**

```python
# services/api/app/schemas/proxmox_cluster.py
"""Pydantic schemas for ProxmoxCluster."""
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services import account_colors

# Operator-chosen natural key: 3–40 chars, lowercase, starts with a letter,
# ends alphanumeric. Encoded in repo paths as `proxmox/cluster-<slug>/…`.
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$"
# `user@realm!tokenid` — the `=` is excluded so a pasted "id=secret" pair is
# rejected instead of silently storing the secret in a plaintext column.
_TOKEN_ID_RE = re.compile(r"^[^\s!@=]+@[^\s!@=]+![^\s!=]+$")


def _https_endpoint(v: str) -> str:
    v = (v or "").strip()
    if not v.lower().startswith("https://"):
        raise ValueError("endpoint must start with https://")
    return v


def _pem(v: Optional[str], header: str) -> Optional[str]:
    if v is None:
        return None
    if v.strip() == "":
        return ""  # "" = clear on update; create treats it as absent
    if not v.lstrip().startswith(f"-----BEGIN {header}"):
        raise ValueError(f"expected a PEM block starting with -----BEGIN {header}")
    return v


class ProxmoxClusterCreate(BaseModel):
    slug: str = Field(..., pattern=SLUG_PATTERN)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    endpoint: str = Field(..., min_length=12, max_length=255)
    api_token_id: str = Field(..., min_length=5, max_length=255)
    api_token_secret: str = Field(..., min_length=8)
    ssh_username: Optional[str] = Field(default=None, max_length=64)
    ssh_private_key: Optional[str] = None
    tls_insecure: bool = False
    ca_cert_pem: Optional[str] = None
    color: Optional[str] = None

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        return account_colors.normalize(v)

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v: str) -> str:
        return _https_endpoint(v)

    @field_validator("api_token_id")
    @classmethod
    def _token_id(cls, v: str) -> str:
        if not _TOKEN_ID_RE.match(v.strip()):
            raise ValueError("api_token_id must look like user@realm!tokenid")
        return v.strip()

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v):
        return _pem(v, "CERTIFICATE") or None

    @field_validator("ssh_private_key")
    @classmethod
    def _key(cls, v):
        # Accept OPENSSH / RSA / EC / generic PRIVATE KEY headers.
        if v is None or v.strip() == "":
            return None
        if "PRIVATE KEY-----" not in v.lstrip()[:64]:
            raise ValueError("ssh_private_key must be a PEM private key")
        return v

    @model_validator(mode="after")
    def _ssh_pair(self):
        if self.ssh_private_key and not (self.ssh_username or "").strip():
            raise ValueError("ssh_username is required when ssh_private_key is set")
        return self


class ProxmoxClusterUpdate(BaseModel):
    """All optional. Secret fields: omitted = unchanged, "" = clear (SSH only)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    endpoint: Optional[str] = Field(default=None, max_length=255)
    api_token_id: Optional[str] = Field(default=None, max_length=255)
    api_token_secret: Optional[str] = Field(default=None, min_length=8)
    ssh_username: Optional[str] = Field(default=None, max_length=64)
    ssh_private_key: Optional[str] = None
    tls_insecure: Optional[bool] = None
    ca_cert_pem: Optional[str] = None
    color: Optional[str] = None

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        return account_colors.normalize(v)

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v):
        return _https_endpoint(v) if v is not None else v

    @field_validator("api_token_id")
    @classmethod
    def _token_id(cls, v):
        if v is None:
            return v
        if not _TOKEN_ID_RE.match(v.strip()):
            raise ValueError("api_token_id must look like user@realm!tokenid")
        return v.strip()

    @field_validator("ca_cert_pem")
    @classmethod
    def _ca(cls, v):
        return _pem(v, "CERTIFICATE")

    @field_validator("ssh_private_key")
    @classmethod
    def _key(cls, v):
        if v is None or v.strip() == "":
            return v
        if "PRIVATE KEY-----" not in v.lstrip()[:64]:
            raise ValueError("ssh_private_key must be a PEM private key")
        return v


class ProxmoxClusterResponse(BaseModel):
    """NEVER carries the token secret or the SSH private key."""
    id: str
    business_unit_id: str
    slug: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    color_effective: str = "gray"
    endpoint: str
    api_token_id: str
    token_secret_masked_tail: str
    ssh_username: Optional[str] = None
    has_ssh_key: bool = False
    tls_insecure: bool = False
    ca_cert_pem: Optional[str] = None

    model_config = {"from_attributes": False}


class ProxmoxClusterTestResult(BaseModel):
    ok: bool
    detail: Optional[str] = None
    version: Optional[str] = None
```

- [ ] **Step 4: Write the router**

```python
# services/api/app/routers/proxmox_clusters.py
"""Proxmox VE cluster CRUD with encrypted API tokens at rest."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.proxmox_cluster import ProxmoxCluster
from app.models.user import User
from app.schemas.proxmox_cluster import (
    ProxmoxClusterCreate,
    ProxmoxClusterResponse,
    ProxmoxClusterTestResult,
    ProxmoxClusterUpdate,
)
from app.services import account_colors
from app.services import proxmox_cluster_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proxmox-clusters", tags=["proxmox-clusters"])


def _to_response(row: ProxmoxCluster) -> ProxmoxClusterResponse:
    # Masked tail is computed from the decrypted secret — cheap, and it means
    # the response reflects a rotation immediately.
    try:
        tail = svc.mask_tail(svc.decrypt_secret(row.api_token_secret_encrypted))
    except RuntimeError:
        tail = "…????"
    return ProxmoxClusterResponse(
        id=row.id,
        business_unit_id=row.business_unit_id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        color=row.color,
        color_effective=account_colors.effective(row.color, row.slug),
        endpoint=row.endpoint,
        api_token_id=row.api_token_id,
        token_secret_masked_tail=tail,
        ssh_username=row.ssh_username or None,
        has_ssh_key=bool(row.ssh_private_key_encrypted),
        tls_insecure=bool(row.tls_insecure),
        ca_cert_pem=row.ca_cert_pem or None,
    )


async def _scoped_cluster(db: AsyncSession, cluster_pk: str, bu: BUScope) -> ProxmoxCluster:
    """Fetch by PK, enforcing the caller's BU scope (404 cross-BU)."""
    row = await db.get(ProxmoxCluster, cluster_pk)
    if row is None or (bu.bu_id is not None and row.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Proxmox cluster not found")
    return row


@router.get("", response_model=list[ProxmoxClusterResponse])
async def list_proxmox_clusters(
    _: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    rows = await svc.list_clusters(db, business_unit_id=bu.bu_id)
    return [_to_response(r) for r in rows]


@router.post("", response_model=ProxmoxClusterResponse, status_code=status.HTTP_201_CREATED)
async def create_proxmox_cluster(
    body: ProxmoxClusterCreate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a cluster",
        )
    existing = (
        await db.execute(
            select(ProxmoxCluster).where(
                ProxmoxCluster.business_unit_id == bu.bu_id,
                ProxmoxCluster.slug == body.slug,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proxmox cluster '{body.slug}' is already configured in this business unit",
        )
    color = await account_colors.assign_for_bu(db, bu.bu_id, body.color)
    row = ProxmoxCluster(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        color=color,
        endpoint=svc.normalize_endpoint(body.endpoint),
        api_token_id=body.api_token_id,
        api_token_secret_encrypted=svc.encrypt_secret(body.api_token_secret),
        ssh_username=(body.ssh_username or "").strip() or None,
        ssh_private_key_encrypted=(
            svc.encrypt_secret(body.ssh_private_key) if body.ssh_private_key else None
        ),
        tls_insecure=body.tls_insecure,
        ca_cert_pem=body.ca_cert_pem or None,
    )
    db.add(row)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proxmox cluster '{body.slug}' is already configured",
        )
    await db.refresh(row)
    return _to_response(row)


@router.put("/{cluster_pk}", response_model=ProxmoxClusterResponse)
async def update_proxmox_cluster(
    cluster_pk: str,
    body: ProxmoxClusterUpdate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    row = await _scoped_cluster(db, cluster_pk, bu)
    data = body.model_dump(exclude_unset=True)

    new_secret = data.pop("api_token_secret", None)
    if new_secret:
        row.api_token_secret_encrypted = svc.encrypt_secret(new_secret)

    # SSH: "" clears, a value re-encrypts, omitted leaves as-is.
    if "ssh_private_key" in data:
        key = data.pop("ssh_private_key")
        row.ssh_private_key_encrypted = svc.encrypt_secret(key) if key else None
    if "ssh_username" in data:
        row.ssh_username = (data.pop("ssh_username") or "").strip() or None
    if row.ssh_private_key_encrypted and not row.ssh_username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ssh_username is required while an SSH private key is stored",
        )

    if "ca_cert_pem" in data:
        row.ca_cert_pem = data.pop("ca_cert_pem") or None
    if "endpoint" in data:
        row.endpoint = svc.normalize_endpoint(data.pop("endpoint"))
    for k, v in data.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/{cluster_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxmox_cluster(
    cluster_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    row = await _scoped_cluster(db, cluster_pk, bu)
    await db.delete(row)
    await db.commit()


@router.post("/{cluster_pk}/test", response_model=ProxmoxClusterTestResult)
async def test_proxmox_cluster(
    cluster_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Probe GET /api2/json/version with the stored token. Never raises for
    remote/auth failures — returns ok=false with a truncated reason."""
    row = await _scoped_cluster(db, cluster_pk, bu)
    try:
        creds = await svc.get_cluster_credentials(db, row.id)
        assert creds is not None
        version = await svc.probe_version(creds)
        return ProxmoxClusterTestResult(
            ok=True, detail=f"Connected — Proxmox VE {version}", version=version
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Proxmox connection test failed for cluster %s", row.slug, exc_info=True)
        return ProxmoxClusterTestResult(ok=False, detail=str(e)[:200])
```

- [ ] **Step 5: Register the router in `main.py`**

In the import block near line 26 (where `gcp_projects,` is), add `proxmox_clusters,`. After `app.include_router(gcp_projects.router)` (line 199) add:

```python
app.include_router(proxmox_clusters.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_proxmox_clusters_router.py -v`
Expected: all pass (7 parametrised validation cases + 8 others = 15 passed)

- [ ] **Step 7: Commit**

```bash
git add services/api/app/schemas/proxmox_cluster.py services/api/app/routers/proxmox_clusters.py services/api/app/main.py services/api/tests/test_proxmox_clusters_router.py
git commit -m "feat(api): /v1/proxmox-clusters CRUD + connection test

Admin-gated, BU-scoped, secrets redacted to a masked tail. /test probes
/api2/json/version with the stored API token honouring the per-cluster TLS
flag and optional CA bundle.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Workspace link — column, schema, validation, path auto-link

**Files:**
- Modify: `services/api/app/models/workspace.py:47-53` (after the `gcp_project_id` column)
- Modify: `services/api/app/schemas/workspace.py:96`, `:139`, `:188` (Create / Update / Response)
- Modify: `services/api/app/routers/workspaces.py:36-92` (regex + helper), `:374-416` (create), `:502-518` (update), `:714-780` (bulk import)
- Test: `services/api/tests/test_workspaces_proxmox_link.py`

**Interfaces:**
- Produces: `Workspace.proxmox_cluster_id: str | None`; `_proxmox_slug_from_path(path) -> str | None`; `proxmox_cluster_id` accepted on create/update and returned on responses.

- [ ] **Step 1: Write the failing tests**

```python
# services/api/tests/test_workspaces_proxmox_link.py
"""Workspace ↔ Proxmox cluster linkage: create/update validation and the
bulk-import path auto-link for `proxmox/cluster-<slug>/<node>/<stack>`."""
import pytest

from app.models.business_unit import DEFAULT_BU_ID
from app.models.proxmox_cluster import ProxmoxCluster
from app.services import proxmox_cluster_service as svc

pytestmark = pytest.mark.usefixtures("default_bu", "default_aws_account")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _cluster(_setup_db, slug="home", bu=DEFAULT_BU_ID):
    async with _setup_db() as s:
        row = ProxmoxCluster(
            business_unit_id=bu, slug=slug, name=slug, endpoint="https://pve:8006",
            api_token_id="u@pam!t", api_token_secret_encrypted=svc.encrypt_secret("x"),
        )
        s.add(row)
        await s.commit()
        return row.id


def test_slug_from_path():
    from app.routers.workspaces import _proxmox_slug_from_path as f

    assert f("proxmox/cluster-home/pve/vm-web") == "home"
    assert f("proxmox/cluster-home-lab2/pve2/lxc/dns") == "home-lab2"
    assert f("PROXMOX/cluster-home/pve/x") == "home"
    assert f("proxmox/home/pve/x") is None
    assert f("gcp/project-p1/us/x") is None
    assert f("") is None


async def test_import_auto_links_registered_cluster(auth_client, admin_token, _setup_db):
    await _cluster(_setup_db, slug="home")
    body = {
        "repo_url": "https://example.com/infra.git", "ref": "main",
        "entries": [
            {"path": "proxmox/cluster-home/pve/vm-web", "name": "vm-web",
             "aws_account_id": "global", "region": "global", "environment": "prod"},
            {"path": "proxmox/cluster-unknown/pve/vm-db", "name": "vm-db",
             "aws_account_id": "global", "region": "global", "environment": "prod"},
        ],
    }
    r = await auth_client.post("/api/v1/workspaces/import", json=body, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    created = {w["name"]: w for w in r.json()["created"]}
    assert created["vm-web"]["proxmox_cluster_id"] is not None
    assert created["vm-web"]["state_backend"] == "s3"
    assert created["vm-db"]["proxmox_cluster_id"] is None


async def test_create_rejects_cluster_from_other_bu(auth_client, admin_token, _setup_db):
    from app.models.business_unit import BusinessUnit

    async with _setup_db() as s:
        s.add(BusinessUnit(id="bu-x", slug="x", name="X"))
        await s.commit()
    other = await _cluster(_setup_db, slug="theirs", bu="bu-x")
    body = {
        "name": "vm", "environment": "dev", "aws_account_id": "global", "region": "global",
        "repo_url": "local://", "tf_working_dir": "proxmox/cluster-theirs/pve/vm",
        "proxmox_cluster_id": other,
    }
    r = await auth_client.post("/api/v1/workspaces", json=body, headers=_h(admin_token))
    assert r.status_code == 400
    assert "Proxmox cluster" in r.text


async def test_update_sets_and_clears_link(auth_client, admin_token, _setup_db):
    mine = await _cluster(_setup_db, slug="mine")
    body = {
        "name": "vm2", "environment": "dev", "aws_account_id": "global", "region": "global",
        "repo_url": "local://", "tf_working_dir": "proxmox/cluster-mine/pve/vm2",
    }
    r = await auth_client.post("/api/v1/workspaces", json=body, headers=_h(admin_token))
    assert r.status_code == 201, r.text
    ws_id = r.json()["id"]
    r = await auth_client.put(f"/api/v1/workspaces/{ws_id}", json={"proxmox_cluster_id": mine}, headers=_h(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["proxmox_cluster_id"] == mine
    r = await auth_client.put(f"/api/v1/workspaces/{ws_id}", json={"proxmox_cluster_id": ""}, headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["proxmox_cluster_id"] is None
    r = await auth_client.put(f"/api/v1/workspaces/{ws_id}", json={"proxmox_cluster_id": "nope"}, headers=_h(admin_token))
    assert r.status_code == 422
```

Note for the implementer: if the existing workspace create test in `tests/test_workspaces*.py` uses a different minimal body (e.g. `aws_account_id` must be a registered account unless `"global"`), copy that body shape. The intent of each assertion is what matters.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_workspaces_proxmox_link.py -v`
Expected: FAIL — `ImportError` for `_proxmox_slug_from_path`, `KeyError: 'proxmox_cluster_id'`.

- [ ] **Step 3: Add the model column**

In `services/api/app/models/workspace.py`, directly after the `gcp_project_id` mapped_column block (ends around line 53), add:

```python
    # Optional Proxmox cluster FK. When set, the executor exports the cluster's
    # API token in both bpg/proxmox and Telmate/proxmox env-var vocabularies.
    # Proxmox has no object store, so state_backend stays "s3" for these rows.
    proxmox_cluster_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("proxmox_clusters.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 4: Add the schema fields**

In `services/api/app/schemas/workspace.py`:

After line 96 (`gcp_project_id: Optional[str] = None` in `WorkspaceCreate`):
```python
    # Optional Proxmox cluster link (PK of proxmox_clusters). Same BU required.
    proxmox_cluster_id: Optional[str] = None
```
After line 139 (in `WorkspaceUpdate`):
```python
    # Same semantics as gcp_project_id: "" clears, a value must be in-BU.
    proxmox_cluster_id: Optional[str] = None
```
After line 188 (in `WorkspaceResponse`):
```python
    # The PK of the linked proxmox_clusters row, or null.
    proxmox_cluster_id: Optional[str] = None
```

- [ ] **Step 5: Add the path helper to the workspaces router**

In `services/api/app/routers/workspaces.py`, after `_GCP_PROJECT_RE` (line 39) add:

```python
# Proxmox leaves live under `proxmox/cluster-<slug>/<node>/<stack>`. The slug is
# the operator-chosen key on proxmox_clusters (Proxmox has no global cluster
# id), so we auto-link on import the same way Azure/GCP do.
_PROXMOX_CLUSTER_RE = re.compile(r"^cluster-([a-z][a-z0-9-]{1,38}[a-z0-9])$")
```

After `_gcp_project_id_from_path` (ends line 92) add:

```python
def _proxmox_slug_from_path(path: str) -> str | None:
    """Extract the cluster slug from a `proxmox/cluster-<slug>/…` path."""
    parts = [p for p in (path or "").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "proxmox":
        m = _PROXMOX_CLUSTER_RE.match(parts[1])
        if m:
            return m.group(1)
    return None
```

- [ ] **Step 6: Validate the link on create**

In `create_workspace`, directly after the GCP block that ends with `gcp_project_pk = proj.id` (line 388), add:

```python
    # Optional Proxmox cluster link: must belong to the same BU.
    proxmox_cluster_pk: str | None = None
    if body.proxmox_cluster_id:
        from app.models.proxmox_cluster import ProxmoxCluster

        pmx = await db.get(ProxmoxCluster, body.proxmox_cluster_id)
        if pmx is None or pmx.business_unit_id != bu.bu_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Proxmox cluster {body.proxmox_cluster_id} is not configured "
                    f"in this business unit"
                ),
            )
        proxmox_cluster_pk = pmx.id
```

In the `Workspace(...)` constructor (around line 416, after `gcp_project_id=gcp_project_pk,`) add:

```python
        proxmox_cluster_id=proxmox_cluster_pk,
```

- [ ] **Step 7: Validate the link on update**

After the GCP override block (ends line 518 with `update_data["gcp_project_id"] = None`), add:

```python
    # proxmox_cluster_id: same semantics — "" clears, a value must be in-BU.
    pmx_override = update_data.get("proxmox_cluster_id")
    if pmx_override:
        from app.models.proxmox_cluster import ProxmoxCluster

        pmx = await db.get(ProxmoxCluster, pmx_override)
        if pmx is None or pmx.business_unit_id != ws.business_unit_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"proxmox_cluster_id '{pmx_override}' is not a registered "
                    f"Proxmox cluster in this Business Unit."
                ),
            )
    elif pmx_override == "":
        update_data["proxmox_cluster_id"] = None
```

- [ ] **Step 8: Auto-link on bulk import**

After the `gcp_by_project_id = {...}` prefetch (ends line 724), add:

```python
    # Same for Proxmox clusters: leaves auto-link by the slug in their path.
    from app.models.proxmox_cluster import ProxmoxCluster

    pmx_by_slug = {
        c.slug: c.id
        for c in (
            await db.execute(
                select(ProxmoxCluster).where(ProxmoxCluster.business_unit_id == bu.bu_id)
            )
        ).scalars().all()
    }
```

After the GCP auto-link (`ws.gcp_project_id = gcp_by_project_id[_gcp_pid]`, line 780) and before `db.add(ws)`, add:

```python
        # Proxmox leaves (proxmox/cluster-<slug>/…): same rule, state stays s3.
        _pmx_slug = _proxmox_slug_from_path(entry.path)
        if _pmx_slug and _pmx_slug in pmx_by_slug:
            ws.proxmox_cluster_id = pmx_by_slug[_pmx_slug]
```

- [ ] **Step 9: Run tests to verify they pass, then the full workspace suite**

Run: `cd services/api && python -m pytest tests/test_workspaces_proxmox_link.py tests/test_repo_discovery.py -v`
Expected: all pass. Then `python -m pytest tests/ -q -k workspace` to catch response-shape regressions.

- [ ] **Step 10: Commit**

```bash
git add services/api/app/models/workspace.py services/api/app/schemas/workspace.py services/api/app/routers/workspaces.py services/api/tests/test_workspaces_proxmox_link.py
git commit -m "feat(api): link workspaces to Proxmox clusters, auto-link on import

proxmox/cluster-<slug>/<node>/<stack> leaves auto-link to the registered
cluster in the same BU, mirroring the Azure/GCP path conventions.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Colour assignment and Slack badge branches

**Files:**
- Modify: `services/api/app/services/account_colors.py:1`, `:118-140`
- Modify: `services/api/app/services/notification_service.py:243-266`
- Test: `services/api/tests/test_proxmox_cluster_service.py` (append two tests)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_proxmox_cluster_service.py`)

```python
async def test_used_colors_span_proxmox(db_session):
    from app.models.business_unit import DEFAULT_BU_ID, BusinessUnit
    from app.models.proxmox_cluster import ProxmoxCluster
    from app.services import account_colors

    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    db_session.add(ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="c", name="c", endpoint="https://p:8006",
        api_token_id="u@pam!t", api_token_secret_encrypted=svc.encrypt_secret("x"),
        color="purple",
    ))
    await db_session.commit()
    assert "purple" in await account_colors.used_colors_for_bu(db_session, DEFAULT_BU_ID)


async def test_slack_badge_resolves_proxmox_cluster(db_session):
    import uuid

    from app.models.business_unit import DEFAULT_BU_ID, BusinessUnit
    from app.models.proxmox_cluster import ProxmoxCluster
    from app.models.workspace import Workspace
    from app.services import account_colors
    from app.services.notification_service import _account_badge

    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    row = ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="lab", name="Lab", endpoint="https://p:8006",
        api_token_id="u@pam!t", api_token_secret_encrypted=svc.encrypt_secret("x"), color="green",
    )
    db_session.add(row)
    await db_session.flush()
    ws = Workspace(
        id=str(uuid.uuid4()), business_unit_id=DEFAULT_BU_ID, name="vm", environment="dev",
        aws_account_id="global", region="global", repo_url="local://",
        tf_working_dir="proxmox/cluster-lab/pve/vm", repo_ref="main", proxmox_cluster_id=row.id,
    )
    db_session.add(ws)
    await db_session.commit()
    badge = await _account_badge(db_session, ws.id)
    assert badge.label == "Lab"
    assert badge.hex == account_colors.COLOR_HEX["green"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_proxmox_cluster_service.py -v -k "colors or badge"`
Expected: `used_colors` test FAILS (`"purple" not in [...]`); badge test FAILS (label empty).

- [ ] **Step 3: Extend `account_colors.used_colors_for_bu`**

Change the import block and the tuple:

```python
    from app.models.aws_account import AwsAccount
    from app.models.azure_subscription import AzureSubscription
    from app.models.gcp_project import GcpProject
    from app.models.k8s_cluster import K8sCluster
    from app.models.proxmox_cluster import ProxmoxCluster

    out: list[str] = []
    for model in (AwsAccount, AzureSubscription, GcpProject, K8sCluster, ProxmoxCluster):
```

Update the docstring's "all four provider tables" to "every provider table (AWS / Azure / GCP / K8s / Proxmox)" and the module docstring's first line to `(AWS / Azure / GCP / K8s / Proxmox)`.

- [ ] **Step 4: Extend the notification badge chain**

In `notification_service.py`, add the import next to `GcpProject`:

```python
    from app.models.proxmox_cluster import ProxmoxCluster
```

and after the `elif ws.gcp_project_id:` branch add:

```python
    elif ws.proxmox_cluster_id:
        row = await session.get(ProxmoxCluster, ws.proxmox_cluster_id)
        fallback = ws.proxmox_cluster_id
```

The existing tail (`key = getattr(row, "account_id", None) or row.id`; label = `row.name`) already handles a cluster row.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_proxmox_cluster_service.py tests/test_notification*.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/api/app/services/account_colors.py services/api/app/services/notification_service.py services/api/tests/test_proxmox_cluster_service.py
git commit -m "feat(api): include Proxmox clusters in BU colour pool and Slack badges

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Executor service — inject `TDT_PROXMOX_*`

**Files:**
- Modify: `services/api/app/services/executor_service.py:333-345` (after the GCP block, before the `_providers` list)
- Test: `services/api/tests/test_executor_launch.py` (append)

**Interfaces:**
- Consumes: `proxmox_cluster_service.get_cluster_credentials()` → `ProxmoxCredentials` (Task 1).
- Produces: env keys `TDT_PROXMOX_ENDPOINT`, `TDT_PROXMOX_TOKEN_ID`, `TDT_PROXMOX_TOKEN_SECRET`, `TDT_PROXMOX_TLS_INSECURE` (`"true"`/`"false"`), optional `TDT_PROXMOX_SSH_USERNAME`, `TDT_PROXMOX_SSH_PRIVATE_KEY`, `TDT_PROXMOX_CA_CERT_PEM`; `proxmox` appended to `TDT_CLOUD_PROVIDERS`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_executor_launch.py`; add `from app.models.proxmox_cluster import ProxmoxCluster` and `from app.services import proxmox_cluster_service as pmxsvc` to the imports at the top)

```python
# ─── proxmox injection ───────────────────────────────────────────────────────


async def test_proxmox_env_injected(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    row = ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="home", name="Home",
        endpoint="https://pve.local:8006", api_token_id="tdt@pve!ci",
        api_token_secret_encrypted=pmxsvc.encrypt_secret("sek"),
        ssh_username="root", ssh_private_key_encrypted=pmxsvc.encrypt_secret("KEY"),
        tls_insecure=True, ca_cert_pem="-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n",
    )
    db_session.add(row)
    await db_session.commit()
    ws, run = await _seed(db_session, proxmox_cluster_id=row.id)
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert env["TDT_PROXMOX_ENDPOINT"] == "https://pve.local:8006"
    assert env["TDT_PROXMOX_TOKEN_ID"] == "tdt@pve!ci"
    assert env["TDT_PROXMOX_TOKEN_SECRET"] == "sek"
    assert env["TDT_PROXMOX_TLS_INSECURE"] == "true"
    assert env["TDT_PROXMOX_SSH_USERNAME"] == "root"
    assert env["TDT_PROXMOX_SSH_PRIVATE_KEY"] == "KEY"
    assert env["TDT_PROXMOX_CA_CERT_PEM"].startswith("-----BEGIN CERTIFICATE-----")
    assert env["TDT_CLOUD_PROVIDERS"] == "aws,proxmox"


async def test_proxmox_optional_fields_absent_when_unset(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    row = ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="bare", name="Bare",
        endpoint="https://pve:8006", api_token_id="u@pam!t",
        api_token_secret_encrypted=pmxsvc.encrypt_secret("s"),
    )
    db_session.add(row)
    await db_session.commit()
    ws, run = await _seed(db_session, proxmox_cluster_id=row.id)
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert env["TDT_PROXMOX_TLS_INSECURE"] == "false"
    for k in ("TDT_PROXMOX_SSH_USERNAME", "TDT_PROXMOX_SSH_PRIVATE_KEY", "TDT_PROXMOX_CA_CERT_PEM"):
        assert k not in env


async def test_proxmox_cred_load_failure_swallowed(db_session, monkeypatch):
    monkeypatch.setenv("EXECUTOR_RUNTIME", "docker")
    monkeypatch.setenv("TERRADUCKTEL_STATE_TOKEN", "t")
    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    row = ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="broken", name="Broken",
        endpoint="https://pve:8006", api_token_id="u@pam!t",
        api_token_secret_encrypted="not-a-valid-fernet-token",
    )
    db_session.add(row)
    await db_session.commit()
    ws, run = await _seed(db_session, proxmox_cluster_id=row.id)
    docker = _Docker()
    await _svc(db_session, docker).launch_run(run, ws, db_session=db_session)
    env = docker.containers.kwargs["environment"]
    assert "TDT_PROXMOX_ENDPOINT" not in env
    assert "proxmox" not in env.get("TDT_CLOUD_PROVIDERS", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/api && python -m pytest tests/test_executor_launch.py -v -k proxmox`
Expected: FAIL with `KeyError: 'TDT_PROXMOX_ENDPOINT'` (first two); third passes trivially — that is fine, it guards the degrade path once code exists.

- [ ] **Step 3: Implement the injection**

In `executor_service.py`, after the GCP block (the `environment.update({... "GOOGLE_REGION": gcp_region})` that ends at line 332) and before the `# Tell the executor entrypoint which provider mix to expect` comment, insert:

```python
        # Proxmox: if the workspace is linked to a Proxmox cluster, pass a
        # canonical TDT_PROXMOX_* set. The entrypoint fans it out to BOTH
        # terraform providers' vocabularies (bpg PROXMOX_VE_*, Telmate PM_*)
        # so one stored credential serves whichever provider the module uses.
        proxmox_pk = getattr(workspace, "proxmox_cluster_id", None)
        if proxmox_pk and db_session is not None:
            try:
                from app.services import proxmox_cluster_service as pmxsvc

                pmx = await pmxsvc.get_cluster_credentials(db_session, proxmox_pk)
            except Exception:
                logger.warning(
                    "Failed to load Proxmox creds for cluster %s — workspace %s will "
                    "fall back to environment auth (likely fails).",
                    proxmox_pk, workspace.id, exc_info=True,
                )
                pmx = None
            if pmx is not None:
                environment.update({
                    "TDT_PROXMOX_ENDPOINT": pmx.endpoint,
                    "TDT_PROXMOX_TOKEN_ID": pmx.token_id,
                    "TDT_PROXMOX_TOKEN_SECRET": pmx.token_secret,
                    "TDT_PROXMOX_TLS_INSECURE": "true" if pmx.tls_insecure else "false",
                })
                if pmx.ssh_username and pmx.ssh_private_key:
                    environment["TDT_PROXMOX_SSH_USERNAME"] = pmx.ssh_username
                    environment["TDT_PROXMOX_SSH_PRIVATE_KEY"] = pmx.ssh_private_key
                if pmx.ca_cert_pem:
                    environment["TDT_PROXMOX_CA_CERT_PEM"] = pmx.ca_cert_pem
```

Then extend the provider list (after `if environment.get("GCP_SA_KEY_JSON"): _providers.append("gcp")`):

```python
        if environment.get("TDT_PROXMOX_ENDPOINT"):
            _providers.append("proxmox")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/api && python -m pytest tests/test_executor_launch.py tests/test_executor_service.py -v`
Expected: all pass, including the pre-existing azure/helm tests.

- [ ] **Step 5: Commit**

```bash
git add services/api/app/services/executor_service.py services/api/tests/test_executor_launch.py
git commit -m "feat(api): inject TDT_PROXMOX_* credentials into executor runs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Executor entrypoint — fan out to both providers + CA bundle

**Files:**
- Modify: `services/executor/entrypoint.sh:56-63` (soft defaults) and `:443-455` (after the GCP auth block)
- Test: `services/executor/tests/test_entrypoint_proxmox.sh` (new; bash, no docker)

**Interfaces:**
- Consumes: the `TDT_PROXMOX_*` env set from Task 5.
- Produces: `PROXMOX_VE_*` and `PM_*` exports, `SSL_CERT_FILE` when a CA is supplied.

- [ ] **Step 1: Verify the env-var names against the provider docs**

Run:
```bash
curl -s https://registry.terraform.io/v1/providers/bpg/proxmox | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["version"])'
```
Then open https://registry.terraform.io/providers/bpg/proxmox/latest/docs and confirm the `PROXMOX_VE_ENDPOINT`, `PROXMOX_VE_API_TOKEN`, `PROXMOX_VE_INSECURE`, `PROXMOX_VE_SSH_USERNAME`, `PROXMOX_VE_SSH_PRIVATE_KEY` names, and https://registry.terraform.io/providers/Telmate/proxmox/latest/docs for `PM_API_URL`, `PM_API_TOKEN_ID`, `PM_API_TOKEN_SECRET`, `PM_TLS_INSECURE`. If any differ, use the documented name in Step 4 and in the docs task; nothing else in the plan depends on these names.

- [ ] **Step 2: Write the failing shell test**

The fan-out lives in a function so it can be sourced without running the whole entrypoint.

```bash
#!/usr/bin/env bash
# services/executor/tests/test_entrypoint_proxmox.sh
# Sources only the proxmox fan-out function from entrypoint.sh and asserts the
# exported provider vocabularies. Run: bash services/executor/tests/test_entrypoint_proxmox.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${HERE}/../entrypoint.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
export HOME="${TMP}/home"; mkdir -p "${HOME}"
# A fake system bundle so the CA merge has something to concatenate.
export TDT_SYSTEM_CA_BUNDLE="${TMP}/system.crt"
printf -- '-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n' > "${TDT_SYSTEM_CA_BUNDLE}"

# Extract just the function body (between the markers) and source it.
sed -n '/^# >>> proxmox_wire_env/,/^# <<< proxmox_wire_env/p' "${ENTRYPOINT}" > "${TMP}/fn.sh"
# shellcheck disable=SC1090
source "${TMP}/fn.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Case 1: token only, insecure.
(
  export TDT_PROXMOX_ENDPOINT="https://pve.local:8006" TDT_PROXMOX_TOKEN_ID="tdt@pve!ci" \
         TDT_PROXMOX_TOKEN_SECRET="sek" TDT_PROXMOX_TLS_INSECURE="true"
  proxmox_wire_env >/dev/null
  [[ "${PROXMOX_VE_ENDPOINT}" == "https://pve.local:8006" ]] || fail "bpg endpoint"
  [[ "${PROXMOX_VE_API_TOKEN}" == "tdt@pve!ci=sek" ]] || fail "bpg token"
  [[ "${PROXMOX_VE_INSECURE}" == "true" ]] || fail "bpg insecure"
  [[ "${PM_API_URL}" == "https://pve.local:8006/api2/json" ]] || fail "telmate url"
  [[ "${PM_API_TOKEN_ID}" == "tdt@pve!ci" && "${PM_API_TOKEN_SECRET}" == "sek" ]] || fail "telmate token"
  [[ "${PM_TLS_INSECURE}" == "true" ]] || fail "telmate insecure"
  [[ -z "${PROXMOX_VE_SSH_USERNAME:-}" && -z "${SSL_CERT_FILE:-}" ]] || fail "no ssh/ca expected"
)

# Case 2: SSH + CA bundle, secure.
(
  export TDT_PROXMOX_ENDPOINT="https://pve.local:8006" TDT_PROXMOX_TOKEN_ID="tdt@pve!ci" \
         TDT_PROXMOX_TOKEN_SECRET="sek" TDT_PROXMOX_TLS_INSECURE="false" \
         TDT_PROXMOX_SSH_USERNAME="root" TDT_PROXMOX_SSH_PRIVATE_KEY="KEYDATA" \
         TDT_PROXMOX_CA_CERT_PEM=$'-----BEGIN CERTIFICATE-----\nCUSTOM\n-----END CERTIFICATE-----'
  # Redirect to a file rather than $(…): command substitution forks, so the
  # exports would not reach the assertions below.
  proxmox_wire_env > "${TMP}/out.txt"
  out="$(cat "${TMP}/out.txt")"
  [[ "${PROXMOX_VE_SSH_USERNAME}" == "root" && "${PROXMOX_VE_SSH_PRIVATE_KEY}" == "KEYDATA" ]] || fail "bpg ssh"
  [[ "${PROXMOX_VE_INSECURE}" == "false" && "${PM_TLS_INSECURE}" == "false" ]] || fail "secure flags"
  [[ -f "${SSL_CERT_FILE}" ]] || fail "SSL_CERT_FILE missing"
  grep -q SYSTEM "${SSL_CERT_FILE}" || fail "system bundle not merged"
  grep -q CUSTOM "${SSL_CERT_FILE}" || fail "custom CA not merged"
  [[ "$(stat -c %a "${SSL_CERT_FILE}")" == "600" ]] || fail "bundle perms"
  [[ "${out}" != *sek* && "${out}" != *KEYDATA* ]] || fail "secret leaked to stdout"
)

echo "OK"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `bash services/executor/tests/test_entrypoint_proxmox.sh`
Expected: `proxmox_wire_env: command not found` (the extracted function is empty).

- [ ] **Step 4: Implement the fan-out in the entrypoint**

Soft defaults — after the GCP defaults block (line 63) add:

```bash
# Proxmox creds are SOFT defaults — only populated when the workspace is
# linked to a proxmox_clusters row. The API sends one canonical TDT_PROXMOX_*
# set; proxmox_wire_env() below fans it out to BOTH terraform providers'
# vocabularies (bpg/proxmox PROXMOX_VE_*, Telmate/proxmox PM_*).
: "${TDT_PROXMOX_ENDPOINT:=}"
: "${TDT_PROXMOX_TOKEN_ID:=}"
: "${TDT_PROXMOX_TOKEN_SECRET:=}"
: "${TDT_PROXMOX_TLS_INSECURE:=false}"
: "${TDT_PROXMOX_SSH_USERNAME:=}"
: "${TDT_PROXMOX_SSH_PRIVATE_KEY:=}"
: "${TDT_PROXMOX_CA_CERT_PEM:=}"
# Overridable so the unit test can point at a fake bundle; the real path is
# Alpine's (hashicorp/terraform base image).
: "${TDT_SYSTEM_CA_BUNDLE:=/etc/ssl/certs/ca-certificates.crt}"
```

Function — immediately after the GCP auth block (`fi` at line 455) and before `report_status "running"`, add. Keep the `# >>>` / `# <<<` marker comments exactly: the test extracts the function by them.

```bash
# >>> proxmox_wire_env
proxmox_wire_env() {
  # bpg/proxmox: origin URL + combined "id=secret" token.
  export PROXMOX_VE_ENDPOINT="${TDT_PROXMOX_ENDPOINT}"
  export PROXMOX_VE_API_TOKEN="${TDT_PROXMOX_TOKEN_ID}=${TDT_PROXMOX_TOKEN_SECRET}"
  export PROXMOX_VE_INSECURE="${TDT_PROXMOX_TLS_INSECURE}"
  # Telmate/proxmox: URL includes the API path, token split in two.
  export PM_API_URL="${TDT_PROXMOX_ENDPOINT}/api2/json"
  export PM_API_TOKEN_ID="${TDT_PROXMOX_TOKEN_ID}"
  export PM_API_TOKEN_SECRET="${TDT_PROXMOX_TOKEN_SECRET}"
  export PM_TLS_INSECURE="${TDT_PROXMOX_TLS_INSECURE}"
  local ssh_note="no"
  if [[ -n "${TDT_PROXMOX_SSH_USERNAME}" && -n "${TDT_PROXMOX_SSH_PRIVATE_KEY}" ]]; then
    export PROXMOX_VE_SSH_USERNAME="${TDT_PROXMOX_SSH_USERNAME}"
    export PROXMOX_VE_SSH_PRIVATE_KEY="${TDT_PROXMOX_SSH_PRIVATE_KEY}"
    ssh_note="yes (${TDT_PROXMOX_SSH_USERNAME})"
  fi
  local ca_note="no"
  if [[ -n "${TDT_PROXMOX_CA_CERT_PEM}" ]]; then
    # Go replaces (not extends) its root pool when SSL_CERT_FILE is set, so
    # merge the system bundle + the custom CA into one file.
    mkdir -p ~/.proxmox
    {
      [[ -r "${TDT_SYSTEM_CA_BUNDLE}" ]] && cat "${TDT_SYSTEM_CA_BUNDLE}"
      printf '\n%s\n' "${TDT_PROXMOX_CA_CERT_PEM}"
    } > ~/.proxmox/bundle.pem
    chmod 600 ~/.proxmox/bundle.pem
    export SSL_CERT_FILE="${HOME}/.proxmox/bundle.pem"
    ca_note="yes"
  fi
  # Tail-only echo — never print the token secret or SSH key.
  echo "=== Proxmox auth wired: ${TDT_PROXMOX_ENDPOINT} as ${TDT_PROXMOX_TOKEN_ID} (tls_insecure=${TDT_PROXMOX_TLS_INSECURE}, ssh=${ssh_note}, custom_ca=${ca_note}) ==="
}
# <<< proxmox_wire_env

if [[ -n "${TDT_PROXMOX_ENDPOINT}" ]]; then
  proxmox_wire_env
fi
```

- [ ] **Step 5: Run the shell test and a syntax check**

Run:
```bash
bash -n services/executor/entrypoint.sh && bash services/executor/tests/test_entrypoint_proxmox.sh
```
Expected: `OK`. If `shellcheck` is installed, also run `shellcheck -S warning services/executor/entrypoint.sh` and fix anything new.

- [ ] **Step 6: Wire the shell test into `make test-api`'s neighbour**

In `Makefile`, after the `test-cli` target, add:

```make
test-executor:
	bash services/executor/tests/test_entrypoint_proxmox.sh
```

- [ ] **Step 7: Commit**

```bash
git add services/executor/entrypoint.sh services/executor/tests/test_entrypoint_proxmox.sh Makefile
git commit -m "feat(executor): export Proxmox creds for bpg and Telmate providers

One TDT_PROXMOX_* credential fans out to PROXMOX_VE_* and PM_*. A custom CA
is merged with the system bundle into SSL_CERT_FILE because Go replaces,
rather than extends, its root pool when that var is set.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: UI foundations — types, path helpers, icon, provider glyph

**Files:**
- Modify: `services/ui/src/components/workspace-tree/types.ts:35`, `:76-83`
- Modify: `services/ui/src/components/workspace-tree/paths.ts:23-33`, `:58-66`
- Modify: `services/ui/src/components/workspace-tree/icons.tsx:88-108`
- Modify: `services/ui/src/components/accountColors.ts:34-42`
- Modify: `services/ui/src/components/AccountTag.tsx:10`, `:19-24`
- Test: `services/ui/src/components/workspace-tree/paths.test.ts`, `services/ui/src/components/AccountTag.test.tsx`

**Interfaces:**
- Produces: `Workspace.proxmox_cluster_id?: string | null`; `ProxmoxClusterLite {id, slug, name}`; `proxmoxInfo(ws) → {slug, node} | null`; `ProxmoxIcon`; `AccountProvider` gains `"proxmox"`.

- [ ] **Step 1: Write the failing tests**

Append to `paths.test.ts` (import `proxmoxInfo` alongside `gcpInfo`):

```ts
describe("proxmoxInfo", () => {
  it("parses slug + node from a proxmox/cluster path", () => {
    const w = ws({ name: "vm-web", region: "global", tf_working_dir: "proxmox/cluster-home/pve/vm-web" });
    expect(proxmoxInfo(w)).toEqual({ slug: "home", node: "pve" });
  });

  it("returns null for a non-proxmox path", () => {
    expect(proxmoxInfo(ws({ name: "vpc", tf_working_dir: "account-123/us-east-1/vpc" }))).toBeNull();
  });

  it("returns null when 'proxmox' is present but the second segment isn't a cluster", () => {
    expect(proxmoxInfo(ws({ name: "x", tf_working_dir: "proxmox/pve/x" }))).toBeNull();
  });

  it("falls back to ws.region when the path omits a node segment", () => {
    const w = ws({ name: "x", region: "global", tf_working_dir: "proxmox/cluster-home" });
    expect(proxmoxInfo(w)).toEqual({ slug: "home", node: "global" });
  });
});
```

Inside the existing `describe("workspacePathSegments", …)` add:

```ts
  it("strips proxmox/cluster-<slug>/<node> so the leaf sits at the node", () => {
    const w = ws({ name: "vm-web", region: "global", tf_working_dir: "proxmox/cluster-home/pve/vm-web" });
    expect(workspacePathSegments(w)).toEqual({ folders: [], leaf: "vm-web" });
  });

  it("keeps intermediate folders under a proxmox node", () => {
    const w = ws({ name: "dns", region: "global", tf_working_dir: "proxmox/cluster-home/pve2/lxc/dns" });
    expect(workspacePathSegments(w)).toEqual({ folders: ["lxc"], leaf: "dns" });
  });
```

In `AccountTag.test.tsx`, extend the provider/label table:

```ts
      ["gcp", "GCP project"],
      ["proxmox", "Proxmox cluster"],
      ["k8s", "Kubernetes cluster"],
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/ui && npx vitest run src/components/workspace-tree/paths.test.ts src/components/AccountTag.test.tsx`
Expected: FAIL — `proxmoxInfo is not a function`; type error / missing label for `proxmox`.

- [ ] **Step 3: Types**

In `types.ts` after the `gcp_project_id?: string | null;` line (35) add:

```ts
  // Proxmox cluster this workspace deploys into. When set, the executor
  // exports the cluster's API token for both bpg/proxmox and Telmate/proxmox.
  // Drives top-level grouping (a cluster gets its own group; the Proxmox
  // node name plays the "region" role).
  proxmox_cluster_id?: string | null;
```

After `GcpProjectLite` (line 83) add:

```ts
export type ProxmoxClusterLite = {
  // TDT primary key (what `workspace.proxmox_cluster_id` stores).
  id: string;
  // Operator-chosen slug (what the `proxmox/cluster-<slug>/` repo path encodes).
  slug: string;
  name: string;
};
```

- [ ] **Step 4: Path helpers**

In `paths.ts` after `gcpInfo` add:

```ts
/**
 * Detect the Proxmox layout encoded in a workspace's repo path:
 * `proxmox/cluster-<slug>/<node>/…`. The node name plays the region role.
 * Path fallback for grouping — the explicit `workspace.proxmox_cluster_id`
 * link wins when present.
 */
export function proxmoxInfo(ws: Workspace): { slug: string; node: string } | null {
  const parts = (ws.tf_working_dir ?? "").trim().split("/").filter(Boolean);
  if (parts[0] !== "proxmox") return null;
  const m = (parts[1] ?? "").match(/^cluster-(.+)$/);
  if (!m) return null;
  return { slug: m[1], node: parts[2] ?? ws.region };
}
```

In `workspacePathSegments`, extend the `else if` chain after the GCP branch:

```ts
  } else if (parts[0] === "proxmox" && /^cluster-/.test(parts[1] ?? "")) {
    // Strip the `proxmox/cluster-<slug>` pair and treat the node as the region.
    parts.shift();
    parts.shift();
    regionToStrip = parts[0] ?? ws.region;
  }
```

- [ ] **Step 5: Icon**

In `icons.tsx` after `GcpIcon` add (update the header comment's "four PROVIDER marks" to "five"):

```tsx
// Proxmox cluster group icon — a server stack in amber, distinct from the
// orange AWS cloud, blue Azure and emerald GCP marks. Stroke-based like GcpIcon.
export function ProxmoxIcon({
  className = "h-4 w-4 text-amber-600 dark:text-amber-400",
}: ProviderIconProps = {}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <rect x="3" y="4" width="18" height="6" rx="1.5" />
      <rect x="3" y="14" width="18" height="6" rx="1.5" />
      <path d="M7 7h.01M7 17h.01" />
    </svg>
  );
}
```

- [ ] **Step 6: Provider type + glyph**

`accountColors.ts`:

```ts
export type AccountProvider = "aws" | "azure" | "gcp" | "proxmox" | "k8s";

export const ACCOUNT_PROVIDER_LABELS: Record<AccountProvider, string> = {
  aws: "AWS account",
  azure: "Azure subscription",
  gcp: "GCP project",
  proxmox: "Proxmox cluster",
  k8s: "Kubernetes cluster",
};
```

`AccountTag.tsx`: import `ProxmoxIcon` and add `proxmox: (c) => <ProxmoxIcon className={c} />,` to `PROVIDER_GLYPH`.

- [ ] **Step 7: Run tests and typecheck**

Run: `cd services/ui && npx vitest run src/components/workspace-tree/paths.test.ts src/components/AccountTag.test.tsx && npx tsc --noEmit`
Expected: tests pass; tsc clean (a `Record<AccountProvider, …>` elsewhere that misses `proxmox` will show up here — `useAccountColors.ts` is fixed in Task 9, so if tsc flags it now, note it and continue).

- [ ] **Step 8: Commit**

```bash
git add services/ui/src/components/workspace-tree/types.ts services/ui/src/components/workspace-tree/paths.ts services/ui/src/components/workspace-tree/paths.test.ts services/ui/src/components/workspace-tree/icons.tsx services/ui/src/components/accountColors.ts services/ui/src/components/AccountTag.tsx services/ui/src/components/AccountTag.test.tsx
git commit -m "feat(ui): Proxmox provider type, path helpers and glyph

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: UI settings page + Cloud Providers tab

**Files:**
- Create: `services/ui/src/pages/ProxmoxClusters.tsx`
- Modify: `services/ui/src/pages/CloudProviders.tsx:1-19`
- Test: `services/ui/src/pages/ProxmoxClusters.test.tsx`

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /v1/proxmox-clusters`, `POST /v1/proxmox-clusters/{id}/test` (Task 2, via the `api` axios client which prepends `/api`).

- [ ] **Step 1: Write the failing test**

```tsx
// services/ui/src/pages/ProxmoxClusters.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: {
    get: vi.fn(async (url: string) => {
      if (url === "/v1/proxmox-clusters") {
        return {
          data: [
            {
              id: "pmx-1", business_unit_id: "bu", slug: "home", name: "Home Lab",
              endpoint: "https://pve.local:8006", api_token_id: "tdt@pve!ci",
              token_secret_masked_tail: "…5555", ssh_username: "root", has_ssh_key: true,
              tls_insecure: true, ca_cert_pem: null, color: null, color_effective: "blue",
            },
          ],
        };
      }
      return { data: [] };
    }),
    post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  },
}));

import ProxmoxClusters from "./ProxmoxClusters";

describe("ProxmoxClusters", () => {
  it("lists clusters with endpoint, token id and masked tail, never the secret", async () => {
    const { container } = render(<ProxmoxClusters />);
    await waitFor(() => expect(screen.getByText("Home Lab")).toBeTruthy());
    expect(screen.getByText(/https:\/\/pve\.local:8006/)).toBeTruthy();
    expect(screen.getByText(/tdt@pve!ci/)).toBeTruthy();
    expect(screen.getByText(/…5555/)).toBeTruthy();
    expect(screen.getByText(/ssh root/)).toBeTruthy();
    expect(screen.getByText(/tls verify off/)).toBeTruthy();
    expect(container.textContent).not.toContain("5555-");
  });
});
```

If the repo's vitest config has no jsdom/testing-library setup for pages, check how `AccountTag.test.tsx` renders (it uses `render` from `@testing-library/react`) and mirror its imports.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/ui && npx vitest run src/pages/ProxmoxClusters.test.tsx`
Expected: FAIL — cannot resolve `./ProxmoxClusters`.

- [ ] **Step 3: Write the page**

```tsx
// services/ui/src/pages/ProxmoxClusters.tsx
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  Input,
  Label,
  Skeleton,
  Spinner,
} from "../components/ui";
import { AccountColorPicker } from "../components/AccountTag";
import { ACCOUNT_COLOR_CLASSES, asAccountColor } from "../components/accountColors";

type ProxmoxCluster = {
  id: string;
  business_unit_id: string;
  slug: string;
  name: string;
  description?: string | null;
  color?: string | null;
  color_effective?: string;
  endpoint: string;
  api_token_id: string;
  token_secret_masked_tail: string;
  ssh_username?: string | null;
  has_ssh_key: boolean;
  tls_insecure: boolean;
  ca_cert_pem?: string | null;
};

type FormState = {
  slug: string;
  name: string;
  description: string;
  endpoint: string;
  api_token_id: string;
  api_token_secret: string;
  ssh_username: string;
  ssh_private_key: string;
  tls_insecure: boolean;
  ca_cert_pem: string;
  color: string;
};

const EMPTY: FormState = {
  slug: "",
  name: "",
  description: "",
  endpoint: "https://",
  api_token_id: "",
  api_token_secret: "",
  ssh_username: "",
  ssh_private_key: "",
  tls_insecure: false,
  ca_cert_pem: "",
  color: "",
};

const TEXTAREA_CLS =
  "block w-full rounded-md border border-brand-border bg-white px-3 py-2 font-mono text-xs " +
  "text-brand-text placeholder-brand-muted transition-colors focus:border-brand-400 focus:outline-none " +
  "focus:ring-2 focus:ring-brand-400/30 dark:border-slate-700/70 dark:bg-slate-950/60 " +
  "dark:text-slate-100 dark:placeholder-slate-500";

function ClusterForm({
  initial,
  editing,
  onSubmit,
  onCancel,
  busy,
  error,
}: {
  initial: FormState;
  editing: boolean;
  onSubmit: (f: FormState) => void;
  onCancel: () => void;
  busy: boolean;
  error: string | null;
}) {
  const [f, setF] = useState<FormState>(initial);
  function update<K extends keyof FormState>(k: K, v: FormState[K]) {
    setF((p) => ({ ...p, [k]: v }));
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit(f);
  }
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>{editing ? "Edit Proxmox cluster" : "Add Proxmox cluster"}</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
          <div>
            <Label>Name</Label>
            <Input value={f.name} onChange={(e) => update("name", e.target.value)} required />
          </div>
          <div>
            <Label>Slug</Label>
            <Input
              placeholder="home-lab"
              value={f.slug}
              onChange={(e) => update("slug", e.target.value)}
              required
              disabled={editing}
              pattern="[a-z][a-z0-9-]{1,38}[a-z0-9]"
              title="lowercase letters, digits and hyphens; used in repo paths as proxmox/cluster-<slug>/"
            />
          </div>
          <div className="md:col-span-2">
            <Label>API endpoint</Label>
            <Input
              placeholder="https://pve.example.com:8006"
              value={f.endpoint}
              onChange={(e) => update("endpoint", e.target.value)}
              required
            />
          </div>
          <div>
            <Label>API token id</Label>
            <Input
              placeholder="terraform@pve!tdt"
              value={f.api_token_id}
              onChange={(e) => update("api_token_id", e.target.value)}
              required
              autoComplete="off"
            />
          </div>
          <div>
            <Label>
              API token secret{" "}
              {editing && <span className="text-xs text-slate-500">(leave blank to keep current)</span>}
            </Label>
            <Input
              type="password"
              value={f.api_token_secret}
              onChange={(e) => update("api_token_secret", e.target.value)}
              required={!editing}
              autoComplete="new-password"
            />
          </div>
          <div className="md:col-span-2 flex items-center gap-2">
            <input
              id="pmx-tls-insecure"
              type="checkbox"
              checked={f.tls_insecure}
              onChange={(e) => update("tls_insecure", e.target.checked)}
            />
            <label htmlFor="pmx-tls-insecure" className="text-sm text-slate-700 dark:text-slate-200">
              Skip TLS certificate verification (self-signed Proxmox certs)
            </label>
          </div>
          <div className="md:col-span-2">
            <Label>CA certificate PEM (optional)</Label>
            <textarea
              className={TEXTAREA_CLS}
              rows={4}
              placeholder="-----BEGIN CERTIFICATE-----"
              value={f.ca_cert_pem}
              onChange={(e) => update("ca_cert_pem", e.target.value)}
              spellCheck={false}
            />
            <p className="mt-1 text-xs text-slate-500">
              Trusted for the connection test and for both Terraform providers at run time.
            </p>
          </div>
          <div className="md:col-span-2 mt-1 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
              SSH access (optional, bpg/proxmox only)
            </p>
            <p className="text-[11px] text-slate-500">
              Needed only for resources that upload files or snippets to a node.
            </p>
          </div>
          <div>
            <Label>SSH username</Label>
            <Input value={f.ssh_username} onChange={(e) => update("ssh_username", e.target.value)} autoComplete="off" />
          </div>
          <div>
            <Label>
              SSH private key{" "}
              {editing && <span className="text-xs text-slate-500">(blank = keep; clear username to remove)</span>}
            </Label>
            <textarea
              className={TEXTAREA_CLS}
              rows={3}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              value={f.ssh_private_key}
              onChange={(e) => update("ssh_private_key", e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="md:col-span-2">
            <Label>Description</Label>
            <Input value={f.description} onChange={(e) => update("description", e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <Label>Color</Label>
            <AccountColorPicker value={f.color} onChange={(c) => update("color", c)} />
          </div>
          {error && (
            <p className="md:col-span-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
              {error}
            </p>
          )}
          <div className="md:col-span-2 mt-2 flex items-center gap-2">
            <Button type="submit" disabled={busy}>{busy ? <Spinner /> : editing ? "Save" : "Add cluster"}</Button>
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

export default function ProxmoxClusters() {
  const [rows, setRows] = useState<ProxmoxCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState<null | { mode: "create" | "edit"; row?: ProxmoxCluster }>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail?: string }>>({});
  const [pendingDelete, setPendingDelete] = useState<ProxmoxCluster | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.get("/v1/proxmox-clusters");
      setRows(r.data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Failed to load Proxmox clusters");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function onSubmit(f: FormState) {
    setBusy(true);
    setFormError(null);
    try {
      const body: any = { ...f };
      body.description = body.description || null;
      body.ca_cert_pem = body.ca_cert_pem || (showForm?.mode === "edit" ? "" : null);
      if (!body.color) delete body.color;
      if (showForm?.mode === "create") {
        if (!body.ssh_username) delete body.ssh_username;
        if (!body.ssh_private_key) delete body.ssh_private_key;
        await api.post("/v1/proxmox-clusters", body);
      } else if (showForm?.mode === "edit" && showForm.row) {
        delete body.slug;
        // Blank secret → keep. Blank key with a username → keep. Blank
        // username → the API clears both.
        if (!body.api_token_secret) delete body.api_token_secret;
        if (!body.ssh_private_key) delete body.ssh_private_key;
        if (!body.ssh_username) body.ssh_private_key = "";
        await api.put(`/v1/proxmox-clusters/${showForm.row.id}`, body);
      }
      setShowForm(null);
      await refresh();
    } catch (e: any) {
      const d = e?.response?.data?.detail;
      setFormError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setActionBusy(true);
    setActionErr(null);
    try {
      await api.delete(`/v1/proxmox-clusters/${pendingDelete.id}`);
      setPendingDelete(null);
      await refresh();
    } catch (e: any) {
      setActionErr(e?.response?.data?.detail ?? "Delete failed");
    } finally {
      setActionBusy(false);
    }
  }

  async function onTest(row: ProxmoxCluster) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Testing…" } }));
    try {
      const r = await api.post(`/v1/proxmox-clusters/${row.id}/test`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: e?.response?.data?.detail ?? "Test failed" } }));
    }
  }

  const initialForm: FormState =
    showForm?.mode === "edit" && showForm.row
      ? {
          slug: showForm.row.slug,
          name: showForm.row.name,
          description: showForm.row.description ?? "",
          endpoint: showForm.row.endpoint,
          api_token_id: showForm.row.api_token_id,
          api_token_secret: "",
          ssh_username: showForm.row.ssh_username ?? "",
          ssh_private_key: "",
          tls_insecure: showForm.row.tls_insecure,
          ca_cert_pem: showForm.row.ca_cert_pem ?? "",
          color: showForm.row.color_effective ?? showForm.row.color ?? "",
        }
      : EMPTY;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Proxmox clusters</h3>
          <p className="text-xs text-slate-500">
            API tokens used by the <code>bpg/proxmox</code> and <code>Telmate/proxmox</code> providers.
            Workspaces at <code>proxmox/cluster-&lt;slug&gt;/&lt;node&gt;/…</code> link automatically on import.
          </p>
        </div>
        {!showForm && (
          <Button onClick={() => { setFormError(null); setShowForm({ mode: "create" }); }}>+ Add cluster</Button>
        )}
      </div>

      {showForm && (
        <ClusterForm
          key={showForm.row?.id ?? "create"}
          initial={initialForm}
          editing={showForm.mode === "edit"}
          onSubmit={onSubmit}
          onCancel={() => setShowForm(null)}
          busy={busy}
          error={formError}
        />
      )}

      {actionErr && (
        <p className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {actionErr}
        </p>
      )}

      {loading ? (
        <Skeleton className="h-24 w-full" />
      ) : err ? (
        <Card><CardBody className="text-sm text-red-600 dark:text-red-300">{err}</CardBody></Card>
      ) : rows.length === 0 ? (
        <EmptyState title="No Proxmox clusters yet" description="Add one to run Terraform against a Proxmox VE cluster." />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => {
            const t = testResult[row.id];
            return (
              <Card key={row.id} className="relative overflow-hidden">
                <span
                  aria-hidden
                  className={
                    "absolute inset-y-0 left-0 w-[3px] " +
                    ACCOUNT_COLOR_CLASSES[asAccountColor(row.color_effective ?? row.color)].solid
                  }
                />
                <CardBody>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{row.name}</span>
                        <Badge tone="info">proxmox</Badge>
                        {row.tls_insecure && <Badge tone="warning">tls verify off</Badge>}
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-500">
                        cluster-{row.slug} · {row.endpoint}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                        token {row.api_token_id} {row.token_secret_masked_tail}
                      </p>
                      {row.has_ssh_key && (
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">ssh {row.ssh_username}</p>
                      )}
                      {row.ca_cert_pem && (
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">custom CA configured</p>
                      )}
                      {row.description && <p className="mt-1 text-xs text-slate-500">{row.description}</p>}
                      {t && (
                        <p className={`mt-2 text-xs ${t.ok ? "text-emerald-700 dark:text-emerald-300" : "text-red-600 dark:text-red-300"}`}>
                          {t.ok ? "✓ " : "✕ "}{t.detail}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button size="sm" variant="ghost" onClick={() => onTest(row)}>Test connection</Button>
                      <Button size="sm" variant="ghost" onClick={() => { setFormError(null); setShowForm({ mode: "edit", row }); }}>Edit</Button>
                      <Button size="sm" variant="danger" onClick={() => { setActionErr(null); setPendingDelete(row); }}>Delete</Button>
                    </div>
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        tone="danger"
        title="Delete Proxmox cluster"
        message={
          <>
            Delete Proxmox cluster <strong>"{pendingDelete?.name}"</strong>? Workspaces linked to
            it will be unlinked and their next run will fail at provider auth.
          </>
        }
        confirmLabel="Delete"
        busy={actionBusy}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
```

- [ ] **Step 4: Add the tab**

In `CloudProviders.tsx`, import `ProxmoxClusters from "./ProxmoxClusters"` and insert after the GCP entry:

```tsx
  { id: "proxmox", label: "Proxmox", render: () => <ProxmoxClusters /> },
```

Update the doc comment's list to include Proxmox.

- [ ] **Step 5: Run the test and typecheck**

Run: `cd services/ui && npx vitest run src/pages/ProxmoxClusters.test.tsx && npx tsc --noEmit`
Expected: PASS; tsc clean apart from anything already flagged in Task 7 Step 7.

- [ ] **Step 6: Commit**

```bash
git add services/ui/src/pages/ProxmoxClusters.tsx services/ui/src/pages/ProxmoxClusters.test.tsx services/ui/src/pages/CloudProviders.tsx
git commit -m "feat(ui): Proxmox tab in Cloud Providers settings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: UI workspace tree, dashboard and run pages

**Files:**
- Modify: `services/ui/src/components/workspace-tree/groups.tsx` (imports, every `gcpProjects` prop site, new `ProxmoxClusterGroup`)
- Modify: `services/ui/src/components/WorkspaceTree.tsx:38-60`, `:83-141`, `:181-260`, `:340-395`
- Modify: `services/ui/src/components/workspace-tree/WorkspaceLeafRow.tsx:13-18`, `:50-63`, `:92-96`, `:457-470`
- Modify: `services/ui/src/hooks/useAccountColors.ts:35-48`, `:80-84`, `:108-111`, `:125-127`
- Modify: `services/ui/src/pages/Dashboard.tsx:26-27`, `:205-206`, `:245-258`, `:628-629`
- Modify: `services/ui/src/pages/Runs.tsx:48`, `services/ui/src/pages/RunDetail.tsx:50`
- Test: existing `paths.test.ts` still passes; manual visual check in Step 8

**Interfaces:**
- Consumes: `ProxmoxClusterLite`, `proxmoxInfo`, `ProxmoxIcon` (Task 7); `GET /v1/proxmox-clusters` (Task 2).

- [ ] **Step 1: Thread the `proxmoxClusters` prop through `groups.tsx`**

Every component that today declares `gcpProjects: GcpProjectLite[]` in its props and forwards `gcpProjects={gcpProjects}` gets a sibling `proxmoxClusters: ProxmoxClusterLite[]` / `proxmoxClusters={proxmoxClusters}`. Add `ProxmoxClusterLite` to the type import and `ProxmoxIcon` to the icon import. Then add after `GcpProjectGroup`:

```tsx
// ─── Cluster group (Proxmox) ───────────────────────────────────────────────────

export function ProxmoxClusterGroup({
  cluster,
  slug,
  ...rest
}: {
  // The registered cluster, when this group is linked/matched to one.
  cluster?: ProxmoxClusterLite;
  // The slug parsed from the repo path when no registration matches.
  slug?: string;
  byRegion: Record<string, Workspace[]>;
  latestByWs: Map<string, Run>;
  defaultOpen: boolean;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
  gcpProjects: GcpProjectLite[];
  proxmoxClusters: ProxmoxClusterLite[];
}) {
  const s = cluster?.slug ?? slug ?? "";
  return (
    <CloudGroupCard
      icon={<ProxmoxIcon />}
      label={
        cluster ? (
          <>
            {cluster.name}{" "}
            <span className="ml-1 font-mono text-xs font-normal text-slate-500">cluster-{s}</span>
          </>
        ) : (
          <span className="font-mono">cluster-{s}</span>
        )
      }
      badge={
        cluster ? (
          <Badge tone="success">configured</Badge>
        ) : (
          <Badge tone="warning">cluster not registered</Badge>
        )
      }
      scopeLabel={cluster ? `${cluster.name} (cluster-${s})` : `cluster ${s}`}
      {...rest}
    />
  );
}
```

Copy the `badge` / `scopeLabel` shape from `GcpProjectGroup` exactly if it differs from the above (read the full component first).

- [ ] **Step 2: `WorkspaceTree.tsx`**

Props: add `proxmoxClusters: ProxmoxClusterLite[]` (import type + `ProxmoxClusterGroup` + `proxmoxInfo`; also add `proxmoxInfo` to the `export { azureInfo, gcpInfo, workspacePathSegments }` line).

Lookup maps, after `gcpByProjectId`:

```tsx
  const pmxByPk = useMemo(() => {
    const m = new Map<string, ProxmoxClusterLite>();
    for (const c of proxmoxClusters) m.set(c.id, c);
    return m;
  }, [proxmoxClusters]);
  const pmxBySlug = useMemo(() => {
    const m = new Map<string, ProxmoxClusterLite>();
    for (const c of proxmoxClusters) m.set(c.slug, c);
    return m;
  }, [proxmoxClusters]);
```

`classify` return type becomes `cloud: "aws" | "azure" | "gcp" | "proxmox"`. After the explicit GCP link branch add:

```tsx
    if (w.proxmox_cluster_id) {
      const c = pmxByPk.get(w.proxmox_cluster_id);
      const info = proxmoxInfo(w);
      return { cloud: "proxmox", key: c ? c.id : w.proxmox_cluster_id, region: info?.node ?? w.region };
    }
```

After the path-detected GCP branch add:

```tsx
    const pinfo = proxmoxInfo(w);
    if (pinfo) {
      const c = pmxBySlug.get(pinfo.slug);
      return { cloud: "proxmox", key: c ? c.id : `slug:${pinfo.slug}`, region: pinfo.node };
    }
```

Filter: add to the searchable array
```tsx
        pmx?.name ?? "",
        pmx?.slug ?? proxmoxInfo(w)?.slug ?? "",
```
where `const pmx = w.proxmox_cluster_id ? pmxByPk.get(w.proxmox_cluster_id) : pmxBySlug.get(proxmoxInfo(w)?.slug ?? "");`, and add `pmxByPk, pmxBySlug` to that memo's deps.

Grouping memo: add `const proxmox: Record<string, Record<string, Workspace[]>> = {};`, extend the bucket selector to `c.cloud === "proxmox" ? proxmox : …`, include `proxmox` in the sort loop, return `proxmoxGrouped: proxmox`, and add `pmxByPk, pmxBySlug` to deps.

Keys + count:

```tsx
  const pmxKeys = Object.keys(proxmoxGrouped).sort((a, b) => {
    const na = pmxByPk.get(a)?.name ?? a;
    const nb = pmxByPk.get(b)?.name ?? b;
    return na.localeCompare(nb);
  });
  const groupCount = accountIds.length + azureKeys.length + gcpKeys.length + pmxKeys.length;
```

Render, after the `gcpKeys.map(...)` block; also pass `proxmoxClusters={proxmoxClusters}` to the AWS, Azure and GCP group renders:

```tsx
            {pmxKeys.map((key) => (
              <ProxmoxClusterGroup
                key={`proxmox:${key}`}
                cluster={pmxByPk.get(key)}
                slug={key.startsWith("slug:") ? key.slice("slug:".length) : undefined}
                byRegion={proxmoxGrouped[key]}
                latestByWs={latestByWs}
                defaultOpen={false}
                onChanged={onChanged}
                expandSignal={expandSignal}
                awsAccounts={awsAccounts}
                azureSubscriptions={azureSubscriptions}
                gcpProjects={gcpProjects}
                proxmoxClusters={proxmoxClusters}
              />
            ))}
```

- [ ] **Step 3: `WorkspaceLeafRow.tsx`**

Add `proxmoxClusters: ProxmoxClusterLite[]` to props (import the type and `proxmoxInfo`). After the GCP detection add:

```tsx
  // Proxmox mirror: auto-derived from the proxmox/cluster-<slug>/ path or the
  // explicit proxmox_cluster_id link. Read-only in the row. No state-backend
  // option is added — Proxmox has no object store, state stays in S3.
  const isProxmox = !!workspace.proxmox_cluster_id || !!proxmoxInfo(workspace);
  const linkedProxmox = proxmoxClusters.find((c) => c.id === workspace.proxmox_cluster_id);
```

After the `{isGcp && (<MetaRow …/>)}` block add:

```tsx
                  {isProxmox && (
                    <MetaRow
                      label="proxmox cluster"
                      title="Auto-derived from the workspace path (proxmox/cluster-<slug>/<node>/…); injects the cluster's API token for the bpg/proxmox and Telmate/proxmox providers."
                    >
                      <span className="font-mono text-[11px]">
                        {linkedProxmox
                          ? `${linkedProxmox.name} (cluster-${linkedProxmox.slug})`
                          : workspace.proxmox_cluster_id
                            ? workspace.proxmox_cluster_id
                            : "(auto-derived from path — cluster not registered)"}
                      </span>
                    </MetaRow>
                  )}
```

- [ ] **Step 4: `useAccountColors.ts`**

Add `proxmox_cluster_id?: string | null;` to the workspace-ish input type, `proxmox: Record<string, AccountBadge>;` to `Maps`, `proxmox: {}` to `EMPTY`, a fifth fetch `api.get("/v1/proxmox-clusters").catch(() => empty)` destructured as `pmx`, `proxmox: build(pmx.data, "proxmox", (r) => r.id),` in the maps, and in the resolver after the GCP line:

```ts
      if (ws.proxmox_cluster_id) return maps.proxmox[ws.proxmox_cluster_id] ?? null;
```

- [ ] **Step 5: `Dashboard.tsx`, `Runs.tsx`, `RunDetail.tsx`**

Dashboard: import `ProxmoxClusterLite`; `const [proxmoxClusters, setProxmoxClusters] = useState<ProxmoxClusterLite[]>([]);`; add a sixth entry to the `Promise.all` — `api.get("/v1/proxmox-clusters").catch(() => ({ data: [] }))` destructured as `pmx`; `setProxmoxClusters(pmx.data);`; pass `proxmoxClusters={proxmoxClusters}` to `<WorkspaceTree>`.

Runs.tsx and RunDetail.tsx: add `proxmox_cluster_id?: string | null;` next to `gcp_project_id` in their local workspace types (so `useAccountColors` resolves the badge).

- [ ] **Step 6: Typecheck and unit tests**

Run: `cd services/ui && npx tsc --noEmit && npx vitest run`
Expected: clean; all vitest suites pass (this is the point where every `Record<AccountProvider, …>` must be complete).

- [ ] **Step 7: Lint**

Run: `cd services/ui && npm run lint` (if the script exists; otherwise `npx eslint src --ext .ts,.tsx`)
Expected: no new warnings. If the grouping memo's `exhaustive-deps` disable comment needs the new maps listed, list them.

- [ ] **Step 8: Visual check**

```bash
docker compose up -d --build api ui
```
Log in as `admin@test.com`, open Settings → Cloud Providers → Proxmox, add a cluster with a dummy `https://127.0.0.1:8006` endpoint and token, click **Test connection** (expect a connection-refused `✕`), then import a repo containing `proxmox/cluster-<slug>/pve/vm-test/main.tf` and confirm the tree shows a Proxmox group with the amber icon, a `pve` node level, and a **proxmox cluster** meta row on the leaf. Delete the dummy cluster afterwards.

- [ ] **Step 9: Commit**

```bash
git add services/ui/src
git commit -m "feat(ui): Proxmox cluster groups in the workspace tree and run badges

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md:113-121` (Cloud targets table), `:126` (Workspace row), `:348-368` (encryption), `:441-470` (executor)
- Modify: `docs/API.md:333-357` (add a section after GCP Projects), `:415` (workspaces PUT row)
- Modify: `CLAUDE.md` Cloud providers bullet

- [ ] **Step 1: ARCHITECTURE.md**

Cloud targets table — add after the `GcpProject` row:

```markdown
| `ProxmoxCluster` | `proxmox_clusters` | Proxmox VE as a provider: operator-chosen `slug` (natural key; Proxmox has no global cluster id), `endpoint`, `api_token_id` + Fernet-encrypted token secret, optional encrypted SSH private key + `ssh_username` (bpg file-upload resources), `tls_insecure` flag, optional `ca_cert_pem`. No state backend — linked workspaces stay on S3. Unique per `(business_unit_id, slug)`. |
```

Workspace row — extend the parenthetical `azure_subscription_id / gcp_project_id (optional Azure/GCP targets)` to `azure_subscription_id / gcp_project_id / proxmox_cluster_id (optional Azure/GCP/Proxmox targets)`.

Encryption — add `b"terraducktel-proxmox-credentials-v1"` to the salt list, `proxmox_cluster_service.py` to the file list, and a bullet `- Proxmox API token secret and optional SSH private key (`proxmox_clusters`).` under "What's encrypted".

Executor — after step 3 in the Terraform list add a sub-note:

```markdown
   Provider credentials are injected per linked account: AWS keys, Azure
   `ARM_*`, GCP `GOOGLE_APPLICATION_CREDENTIALS`, and for Proxmox a canonical
   `TDT_PROXMOX_*` set that the entrypoint fans out to **both**
   `bpg/proxmox` (`PROXMOX_VE_*`) and `Telmate/proxmox` (`PM_*`) so one stored
   token serves either provider. A custom CA is merged with the system bundle
   into `SSL_CERT_FILE` (Go replaces, not extends, its root pool).
```

Also add the repo path convention next to wherever `gcp/project-<id>/<region>/<stack>` is documented (grep for it): `proxmox/cluster-<slug>/<node>/<stack>` — the node name plays the region role; discovery stamps `global/global` like other non-AWS paths and the UI reads the node from the path.

- [ ] **Step 2: API.md**

Insert after the GCP Projects section (before `## Kubernetes Clusters`):

```markdown
## Proxmox Clusters — `/api/v1/proxmox-clusters`

Encrypted-at-rest Proxmox VE API tokens, mirroring the other providers.
Workspaces that target `bpg/proxmox` or `Telmate/proxmox` link one of these;
the executor exports both providers' env-var vocabularies from the one token.

| Method | Path | Description | Min role | BU |
|---|---|---|---|---|
| GET | `/proxmox-clusters` | List clusters (secret + SSH key never returned; masked tail shown). | viewer | BU-scoped |
| POST | `/proxmox-clusters` | Add a cluster (token secret / SSH key stored encrypted). | admin | BU-scoped |
| PUT | `/proxmox-clusters/{cluster_pk}` | Update fields, rotate the token secret, set/clear SSH key, TLS flag, CA PEM. | admin | — |
| DELETE | `/proxmox-clusters/{cluster_pk}` | Delete; linked workspaces are unlinked (FK SET NULL). | admin | — |
| POST | `/proxmox-clusters/{cluster_pk}/test` | `GET /api2/json/version` with the stored token; honours `tls_insecure` / `ca_cert_pem`. Returns `{ok, detail, version?}`. | admin | — |

**POST /proxmox-clusters** body: `{slug, name, description?, endpoint,
api_token_id, api_token_secret, ssh_username?, ssh_private_key?,
tls_insecure?, ca_cert_pem?, color?}`. `slug` matches
`^[a-z][a-z0-9-]{1,38}[a-z0-9]$` and is unique per BU (**409** on duplicate).
`endpoint` must be `https://` (trailing `/api2/json` is stripped).
`api_token_id` is `user@realm!tokenid`; an `=` in it is rejected (**422**) so a
pasted `id=secret` pair never lands in a plaintext column. `ssh_private_key`
requires `ssh_username`. Responses carry `token_secret_masked_tail` and
`has_ssh_key` in place of the secrets. Workspaces at
`proxmox/cluster-<slug>/<node>/<stack>` auto-link to the matching cluster on
import; `state_backend` stays `s3`.
```

Workspaces PUT row: add `proxmox_cluster_id` to the list of updatable fields.

- [ ] **Step 3: CLAUDE.md**

Extend the Cloud providers bullet: after `GCP (gcp_projects, google via a service-account key → GOOGLE_APPLICATION_CREDENTIALS)` add `, and Proxmox VE (proxmox_clusters, API token exported for both bpg/proxmox and Telmate/proxmox; no state backend)`; and after `aws_account_id / azure_subscription_id / gcp_project_id` add `/ proxmox_cluster_id`.

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md docs/API.md CLAUDE.md
git commit -m "docs: document the Proxmox provider slice

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Reconcile the spec with the three implementation findings

**Files:**
- Modify: `docs/superpowers/specs/2026-09-12-proxmox-provider-design.md` §1 (workspace link), §2, §4 (cross-cutting)

- [ ] **Step 1: Edit the spec**

§1 "Workspace link": replace `The \`region\` column holds the Proxmox **node name** (e.g. \`pve\`, \`pve2\`); it is already free-form for Azure so no validation change is needed.` with:

```markdown
The `region` column stays `"global"`, as repo discovery already stamps for
every non-AWS path. The Proxmox **node name** is the path's third segment and
the UI reads it from there (the same way it reads Azure/GCP regions).
```

§2: replace `Bulk import and the repo-sync loop auto-link a leaf` with `Bulk import auto-links a leaf` and delete `- \`<node>\` is written to \`workspace.region\`.`; replace it with `- \`<node>\` is read from the path by the UI; \`workspace.region\` stays \`global\`.`

§4 cross-cutting paragraph: replace the sentence `\`account_colors.py\` needs no code change; only its docstring lists providers.` with `\`account_colors.used_colors_for_bu()\` gains \`ProxmoxCluster\` in its provider-table tuple so colours stay unique BU-wide.`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-12-proxmox-provider-design.md
git commit -m "docs(spec): align Proxmox design with discovery and colour-pool behaviour

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: End-to-end validation against a real Proxmox host (manual)

**Files:** none committed. A throwaway sample repo lives in the scratchpad.

Prerequisite: the operator's Proxmox host (192.168.0.50) and an API token from 1Password. Ask the user to run the 1Password step themselves with `! op ...` if the CLI prompts.

- [ ] **Step 1: Bring the stack up and register the cluster**

```bash
make up && make seed-db
```
In Settings → Cloud Providers → Proxmox add: slug `home`, endpoint `https://192.168.0.50:8006`, the token id/secret, **Skip TLS verification** on. Click **Test connection** → expect `✓ Connected — Proxmox VE <version>`.

- [ ] **Step 2: Create a two-leaf sample repo**

```
proxmox/cluster-home/pve/probe-bpg/main.tf
proxmox/cluster-home/pve/probe-telmate/main.tf
```

`probe-bpg/main.tf`:
```hcl
terraform {
  required_providers { proxmox = { source = "bpg/proxmox", version = ">= 0.60" } }
}
provider "proxmox" {}   # everything comes from PROXMOX_VE_* env
data "proxmox_virtual_environment_nodes" "all" {}
output "nodes" { value = data.proxmox_virtual_environment_nodes.all.names }
```

`probe-telmate/main.tf`:
```hcl
terraform {
  required_providers { proxmox = { source = "Telmate/proxmox", version = ">= 3.0.1-rc1" } }
}
provider "proxmox" {}   # everything comes from PM_* env
# No data sources for nodes in Telmate; a provider-config-only plan still
# authenticates during `terraform plan` (the provider validates on configure).
```

Push it to Forgejo (http://localhost:3002) and import via **Sync from repo**. Both leaves must appear under a **Home** Proxmox group at node `pve`, and the `probe-bpg` leaf's expanded meta must show `proxmox cluster: Home (cluster-home)`.

- [ ] **Step 3: Run a plan on each leaf**

Trigger **Plan** on both. Expected: both reach `awaiting_approval`; the run log shows `=== Proxmox auth wired: https://192.168.0.50:8006 as … (tls_insecure=true, ssh=no, custom_ca=no) ===` and no token secret anywhere in the log; the bpg plan's outputs list the node names; the Telmate plan shows "No changes". Do **not** approve/apply — nothing to apply.

- [ ] **Step 4: Record the outcome**

Paste the two run ids and the wired-auth log line into the PR description. Delete the sample repo and the `home` cluster row afterwards if the user does not want to keep them.

---

## Self-review checklist (done while writing)

- **Spec coverage:** §1 → Tasks 1, 3; §2 → Tasks 3, 7, 9; §3 → Tasks 5, 6; §4 → Tasks 2, 4; §5 → Tasks 7, 8, 9; §6 error handling → Tasks 2 (test endpoint never 500s), 5 (degrade), entrypoint tail-only echo; §7 testing → each task's tests + Task 12; §8 docs → Task 10; spec corrections → Task 11.
- **Type consistency:** `ProxmoxCredentials` fields (`endpoint, token_id, token_secret, tls_insecure, ssh_username, ssh_private_key, ca_cert_pem`) are used identically in Tasks 1, 2 and 5. `ProxmoxClusterLite {id, slug, name}` is used identically in Tasks 7 and 9. `proxmoxInfo` returns `{slug, node}` in Tasks 7 and 9. Response keys in Task 2 match the page type in Task 8.
- **Out of scope, unchanged:** no state backend, no discovery change, no workspace-form picker, no cost changes.
