# Security Policy

> **Pre-1.0 software.** Terraducktel is under active development. APIs,
> schemas, and defaults may change between releases without a deprecation
> period, and the project has not yet had an independent security audit.
> Review the code and threat model before relying on it for production
> infrastructure changes, and keep up with releases.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

Instead, open a **private GitHub Security Advisory** on this repository
(repository → **Security** tab → **Report a vulnerability**). This creates a
private channel between you and the maintainers to discuss, reproduce, and
fix the issue before any public disclosure.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce (a minimal `docker compose` setup is ideal, since the
  whole stack runs that way).
- The affected component (`services/api`, `services/ui`, `services/executor`,
  `services/drift-detector`, etc.) and version/commit if known.

We'll acknowledge new advisories and follow up with next steps as we
triage. Coordinated disclosure timelines are worked out case-by-case in the
advisory thread.

## Security model summary

- **Credential encryption at rest.** AWS account credentials, cluster
  kubeconfigs, and integration tokens stored in Postgres are encrypted with
  Fernet, keyed by an HKDF-derived key sourced from the `CREDENTIAL_ENCRYPTION_KEY`
  env var. Plaintext credentials never leave the API process — integration
  "get" endpoints only ever return `{configured: bool, masked_tail}`.
- **Authentication & authorization.** JWT-based sessions, with OIDC support
  for federating to an external identity provider. Authorization is RBAC with
  a strict role hierarchy (`viewer < operator < admin`), enforced per-endpoint
  via `require_role`. Multi-tenancy is scoped by Business Unit: every
  workspace and AWS account belongs to exactly one BU, and non-superadmin
  users only see the BUs they're a member of. API keys carry their own
  capability tier (`read` / `plan` / `apply` / `admin`) and can never exceed
  their owning user's role.
- **Policy gating on every plan.** Every Terraform plan is run through
  Checkov and OPA policy bundles before it can be approved. Applies require
  explicit human approval of the reviewed plan — there is no direct
  plan-to-apply path that skips review.
- **Tamper-evident audit log.** All state-changing actions are recorded in an
  append-only audit log using a SHA-256 hash chain (`entry_hash =
  sha256(prev_hash || canonical-row-json)`), so a modified or deleted history
  entry breaks the chain and is detectable.
- **Secret scrubbing.** Terraform/Helm plan and apply output is scrubbed for
  known credential patterns before being stored or displayed, to reduce the
  chance of a leaked secret ending up in run logs.

## Supported versions

Pre-1.0: only the `dev`/latest branch and most recent tagged release are
supported with fixes. There is no long-term-support branch yet.
