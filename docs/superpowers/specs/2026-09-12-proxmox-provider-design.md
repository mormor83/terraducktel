# Proxmox provider support — design

**Date:** 2026-09-12
**Status:** approved design, pending implementation plan
**Branch:** `proxmox_support`

## Problem

Terraducktel (TDT) runs Terraform workspaces against AWS, Azure and GCP. Each
provider is a per-Business-Unit vertical slice: an encrypted credential row,
a workspace link, executor env-var injection, and a Cloud Providers UI tab.
Operators who run Proxmox VE want to manage VMs, LXC containers, storage and
networking through the same `plan → checkov → approval → apply` pipeline,
with the same BU scoping, RBAC and audit trail.

## Decision summary

Add Proxmox as a fourth dedicated provider slice, copied from the GCP slice
(migration 038). Rejected alternatives: a generic "custom provider" env-var
bag (no typed validation, no connection test, second code path everywhere)
and "just use workspace secret variables" (per-workspace credentials, no
rotation story, invisible in the UI).

Decisions taken during brainstorming:

| Question | Decision |
|---|---|
| Terraform provider | Support both `bpg/proxmox` and `Telmate/proxmox`. One stored credential is exported in both providers' env-var vocabularies. |
| Authentication | API token (`user@realm!tokenid` + secret). Optional SSH username + private key for bpg resources that need node SSH (file/snippet uploads). No password auth. |
| TLS | Per-cluster `tls_insecure` flag (default off) plus an optional CA bundle in PEM. |
| State backend | None new. Proxmox workspaces keep `state_backend=s3`, like Azure/GCP workspaces that do not opt into their own object store. |
| Drift | Plan-based only (already works through the executor). The AWS live tag scan keeps skipping non-AWS workspaces. |
| Cost | No change. infracost prices unknown resources at zero. |

## 1. Data model

### New table `proxmox_clusters`

Alembic revision `044_proxmox_clusters`. Model in
`services/api/app/models/proxmox_cluster.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | String PK | uuid4 |
| `business_unit_id` | String, NOT NULL | Owning BU |
| `slug` | String(40), NOT NULL | Operator-chosen natural key: `^[a-z][a-z0-9-]{1,38}[a-z0-9]$`. Unique per `(business_unit_id, slug)`. Encoded in repo paths (see §2). Proxmox has no globally unique cluster id, so this plays the role GCP's `project_id` plays. |
| `name` | String(120), NOT NULL | Display name |
| `description` | Text, nullable | |
| `color` | String(16), nullable | Palette token from `account_colors`; NULL = derived |
| `endpoint` | String(255), NOT NULL | `https://host:8006`. Scheme must be `https`; trailing slash and any `/api2/json` suffix are stripped on write. |
| `api_token_id` | String(255), NOT NULL | `user@realm!tokenid`. Identifier, not secret; stored plain, returned in responses. |
| `api_token_secret_encrypted` | Text, NOT NULL | Fernet, HKDF salt `b"terraducktel-proxmox-credentials-v1"` |
| `ssh_username` | String(64), nullable | For bpg SSH-backed resources |
| `ssh_private_key_encrypted` | Text, nullable | Same Fernet context. PEM private key. |
| `tls_insecure` | Boolean, NOT NULL, default false | Skip certificate verification |
| `ca_cert_pem` | Text, nullable | PEM CA bundle. Public material, stored plain. |
| `created_at`, `updated_at` | timestamptz | server defaults, as siblings |

Constraints: `UNIQUE (business_unit_id, slug)`. `ssh_private_key_encrypted`
requires `ssh_username` (validated in the schema, not the DB).

### Workspace link

Same revision adds `workspaces.proxmox_cluster_id` — String, nullable,
`ForeignKey("proxmox_clusters.id", ondelete="SET NULL")`. Proxmox workspaces
use `aws_account_id="global"` (the
existing non-AWS convention) and `state_backend="s3"`. The `region` column
stays `"global"`, as repo discovery already stamps for every non-AWS path.
The Proxmox **node name** is the path's third segment and the UI reads it
from there (the same way it reads Azure/GCP regions).

`WorkspaceResponse`, `WorkspaceCreate` and `WorkspaceUpdate` gain
`proxmox_cluster_id: str | None`. Create/update validate that a supplied
cluster belongs to the caller's BU (400 otherwise), mirroring the GCP branch
in `routers/workspaces.py`.

## 2. Repo path convention and auto-linking

Providers are linked on import, by path; there is no picker on the workspace
form. Proxmox leaves live at:

```
proxmox/cluster-<slug>/<node>/<stack>
```

- `routers/workspaces.py` gains `_PROXMOX_CLUSTER_RE` and
  `_proxmox_slug_from_path()`. Bulk import auto-links a leaf when `<slug>`
  matches a `proxmox_clusters` row in the same BU. An unregistered slug leaves
  the workspace unlinked; registering the cluster and re-running **Sync from
  repo** links it.
- `<node>` is read from the path by the UI; `workspace.region` stays `global`.
- UI `paths.ts` gains `proxmoxInfo(ws)` and strips the `proxmox/cluster-<slug>`
  pair plus the node in `workspacePathSegments`, exactly as the GCP branch does.
- The tree groups Proxmox clusters at top level (like GCP projects) with nodes
  as the second level (like regions).

## 3. Executor wiring

### API side (`services/executor_service.py`)

After the GCP branch: if `workspace.proxmox_cluster_id` is set, load the row
via `proxmox_cluster_service.get_cluster_credentials()` and inject a
**canonical, TDT-internal** env set:

| Env var | Source |
|---|---|
| `TDT_PROXMOX_ENDPOINT` | `endpoint` |
| `TDT_PROXMOX_TOKEN_ID` | `api_token_id` |
| `TDT_PROXMOX_TOKEN_SECRET` | decrypted secret |
| `TDT_PROXMOX_TLS_INSECURE` | `"true"` / `"false"` |
| `TDT_PROXMOX_SSH_USERNAME` | `ssh_username`, injected only when both it and `ssh_private_key` are set |
| `TDT_PROXMOX_SSH_PRIVATE_KEY` | decrypted key, injected only when both it and `ssh_username` are set |
| `TDT_PROXMOX_CA_CERT_PEM` | `ca_cert_pem` or unset |

Append `proxmox` to `TDT_CLOUD_PROVIDERS`. On credential-load failure, log a
warning and continue without them (same degrade as GCP).

### Entrypoint side (`services/executor/entrypoint.sh`)

A block after the GCP one, guarded by `[[ -n "${TDT_PROXMOX_ENDPOINT}" ]]`,
fans the canonical set out to both providers:

- **bpg/proxmox:** `PROXMOX_VE_ENDPOINT`, `PROXMOX_VE_API_TOKEN`
  (`<token_id>=<secret>`), `PROXMOX_VE_INSECURE`, and when SSH is configured
  `PROXMOX_VE_SSH_USERNAME` + `PROXMOX_VE_SSH_PRIVATE_KEY`.
- **Telmate/proxmox:** `PM_API_URL` (`<endpoint>/api2/json`), `PM_API_TOKEN_ID`,
  `PM_API_TOKEN_SECRET`, `PM_TLS_INSECURE`.
- **CA bundle:** when `TDT_PROXMOX_CA_CERT_PEM` is set, concatenate the
  system bundle (`/etc/ssl/certs/ca-certificates.crt` on the Alpine-based
  `hashicorp/terraform` image, overridable via `TDT_SYSTEM_CA_BUNDLE`) plus
  the custom PEM into a single `~/.proxmox/bundle.pem` (0600) — there is no
  separate `ca.pem` file — and export `SSL_CERT_FILE` pointing at it. Go
  replaces rather than extends the root pool when `SSL_CERT_FILE` is set,
  which is why the system bundle is merged in. If the system bundle isn't
  readable, the entrypoint logs a `WARN` and the merged bundle contains only
  the custom CA. The exact system bundle path and both providers' env-var
  names are verified against the image and provider docs during
  implementation.
- Echo a tail-only line (`endpoint`, token id, whether SSH/CA are wired). Never
  print the secret or key.

The executor image needs no new binaries: both providers are Go plugins
fetched by `terraform init`.

## 4. API surface

Router `routers/proxmox_clusters.py`, prefix `/v1/proxmox-clusters`, registered
in `main.py`. All endpoints `require_role(Role.admin)` and are scoped by
`current_bu` (superadmin with header `all` sees every BU).

| Method | Path | Notes |
|---|---|---|
| GET | `` | List clusters in the current BU |
| POST | `` | Create. Stamps `business_unit_id` from `current_bu`. 409 on duplicate slug. |
| PUT | `/{pk}` | Update. Secret fields are optional; omitted = unchanged. |
| DELETE | `/{pk}` | 204. Linked workspaces get `proxmox_cluster_id=NULL` via FK. |
| POST | `/{pk}/test` | Connection test: `GET {endpoint}/api2/json/version` with header `Authorization: PVEAPIToken=<token_id>=<secret>`, httpx, 10 s timeout, TLS per `tls_insecure` / `ca_cert_pem`. Returns `{ok, detail, version?}`. Never raises for auth/network failures; returns `ok=false` with a truncated message. |

Response schema returns `api_token_id`, `ssh_username`, `tls_insecure`,
`ca_cert_pem`, plus `has_ssh_key: bool` and `token_secret_masked_tail`. It
never returns the secret or private key.

Schemas in `schemas/proxmox_cluster.py`: `ProxmoxClusterCreate`,
`ProxmoxClusterUpdate`, `ProxmoxClusterResponse`, `ProxmoxClusterTestResult`.
Validation: slug regex, token id matches `^[^\s!@=]+@[^\s!@=]+![^\s!=]+$`
(the `=` is excluded so a pasted `id=secret` pair is rejected instead of
silently landing in the plaintext `api_token_id` column), PEM fields start
with the expected header line, SSH key requires username. `endpoint` must be
an https origin: parsed with `urllib.parse.urlsplit`, `scheme == "https"` and
a non-empty host are required, query strings and fragments are rejected
(422) — this catches a pasted Proxmox UI URL such as
`https://pve:8006/#v1:0:18` — and a trailing `/api2/json` is stripped so the
DB always holds the bare origin.

Service `services/proxmox_cluster_service.py`: `_fernet()` with the Proxmox
salt, `encrypt_secret`/`decrypt_secret`, `list_clusters`, `get_cluster`,
`get_cluster_credentials(session, pk) -> ProxmoxCredentials | None` (a small
dataclass carrying endpoint, token id, secret, tls flag, ssh user/key, ca pem).

Cross-cutting touches (one branch each): `notification_service.py` account
tag resolution (a `ws.proxmox_cluster_id` branch beside the GCP one) and a
`_scoped_cluster()` helper inside the new router that 404s on a cluster outside
the caller's BU, mirroring `_scoped_project()` in the GCP router.
`account_colors.used_colors_for_bu()` gains `ProxmoxCluster` in its provider-table
tuple so colours stay unique BU-wide.

## 5. UI

- `pages/ProxmoxClusters.tsx`: copy of `GcpProjects.tsx`. Fields: name, slug,
  endpoint, token id, token secret (write-only), SSH username, SSH private key
  (write-only textarea), TLS skip-verify checkbox, CA PEM textarea, colour,
  description. Buttons: Save, Test connection, Delete.
- `pages/CloudProviders.tsx`: add `{ id: "proxmox", label: "Proxmox" }` tab.
- `workspace-tree/icons.tsx`: `ProxmoxIcon` (stroke-based server/rack mark,
  default colour via a Tailwind token, no hard-coded hex).
- `AccountTag.tsx` and `accountColors.ts`: add `proxmox` to `AccountProvider`
  and the glyph map.
- `workspace-tree/types.ts`: `proxmox_cluster_id` on `Workspace`,
  `ProxmoxClusterLite {id, slug, name}`.
- `workspace-tree/paths.ts`, `groups.tsx`, `WorkspaceTree.tsx`,
  `WorkspaceLeafRow.tsx`, `useAccountColors.ts`, `Runs.tsx`, `RunDetail.tsx`,
  `Dashboard.tsx`: add the Proxmox branch wherever the GCP branch exists.

## 6. Error handling

- Missing/invalid credentials at run time: executor falls back to environment
  auth and the provider fails at `terraform init`/`plan` with the provider's own
  error, visible in the run log. The API logs a warning naming the workspace.
- Connection test failures (DNS, TLS, 401) return `ok=false` with the reason;
  the endpoint never 500s on remote errors.
- SSRF posture: the test endpoint is admin-only and HTTPS-only, matching the
  risk profile of the existing Azure/GCP test endpoints. No further allowlisting
  in this change.
- Deleting a cluster that workspaces link to is allowed; the FK nulls the link
  and the workspaces' next run fails at provider auth. Same as GCP today.

## 7. Testing

**Backend (`make test-api`):**
- `tests/test_proxmox_clusters_router.py`, copied from the GCP router tests:
  CRUD, duplicate slug → 409, BU isolation (a cluster in BU A is invisible and
  un-updatable from BU B), viewer/operator → 403, secret never in responses,
  test endpoint against an `httpx.MockTransport` returning a version payload,
  a 401, and a connection error.
- `tests/test_proxmox_cluster_service.py`: encrypt/decrypt round trip, distinct
  ciphertext from the GCP context for the same plaintext.
- Extend `tests/test_executor_service.py`: a workspace linked to a Proxmox
  cluster yields the canonical `TDT_PROXMOX_*` env set and `proxmox` in
  `TDT_CLOUD_PROVIDERS`; an unlinked workspace yields none of them.
- Extend the workspaces router tests: bulk import auto-links a
  `proxmox/cluster-<slug>/…` leaf to a registered cluster and leaves an
  unregistered slug unlinked; `region` equals the node segment.

**Frontend (`make test-ui`):**
- `paths.test.ts`: `proxmoxInfo` parses the convention; `workspacePathSegments`
  strips the cluster pair and node.
- `AccountTag.test.tsx`: renders the Proxmox glyph for `provider="proxmox"`.

**End to end (manual, once):** register the operator's Proxmox host
(192.168.0.50, token from 1Password) in a dev BU, run the connection test,
import a two-leaf sample repo (one leaf per Terraform provider) and drive
one `plan` through each to prove both env-var vocabularies authenticate.
Recorded as a checklist item in the implementation plan, not automated.

## 8. Documentation

- `docs/ARCHITECTURE.md`: add `ProxmoxCluster` to the model table, extend the
  Cloud Providers and Executor sections, add the HKDF salt to the encryption
  section, add the repo path convention next to the GCP one.
- `docs/API.md`: document `/v1/proxmox-clusters`.
- `CLAUDE.md`: extend the Cloud providers convention bullet with Proxmox.

## Out of scope

- A Proxmox-backed state store.
- Live (non-plan) drift scanning of Proxmox resources.
- Password authentication or ticket-based auth.
- Cost estimation for Proxmox resources.
- A provider picker on the workspace create form.
