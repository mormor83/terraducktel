"""Discover Terraform stack hierarchies in a Git repository.

Convention (account / region / leaf layout):

    account-<aws_account_id>/
      <region>/
        <leaf-folder>/      # one Terraform stack — gets its own tfstate

Each leaf folder containing a `*.tf` file becomes a candidate workspace.
The discovery is read-only — it shallow-clones the repo into a tempdir,
walks the tree, and returns a structured tree the UI can render.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from typing import Iterable, Optional

ACCOUNT_DIR_RE = re.compile(r"^account-(\d{6,14})$")
REGION_DIR_RE = re.compile(
    r"^(us|eu|ap|sa|ca|me|af)-(north|south|east|west|central|northeast|southeast|northwest|southwest)-\d$"
)


@dataclass
class StackCandidate:
    path: str  # repo-relative path, slash-separated
    name: str  # leaf folder name (display label)
    aws_account_id: str
    region: str
    suggested_environment: str
    has_tf: bool = True
    # Workspace kind: "terraform" (default) or "helm". A directory with a
    # Chart.yaml and no *.tf files is a Helm chart. Helm releases have no
    # external state backend (state lives in-cluster), so state_key is unused
    # for helm candidates — but it's still populated for path uniqueness.
    kind: str = "terraform"
    # Explicit S3 state-key suffix written to workspaces.state_key on import.
    # Encodes the unique path (account/region/env/<intermediate folders>/leaf)
    # so two workspaces with the same leaf name in different parent folders
    # don't collide on S3 state. Migration 019 introduced the column; older
    # workspaces leave it NULL and resolve via the legacy {account}/{region}/
    # {env}/{name} formula in Workspace.state_path.
    state_key: str = ""


@dataclass
class DiscoveryAccount:
    aws_account_id: str
    regions: dict[str, list[StackCandidate]] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    repo_url: str
    ref: str
    stacks: list[StackCandidate]
    accounts: list[DiscoveryAccount]
    errors: list[str]


_ENV_KEYWORDS: dict[str, str] = {
    "prod": "prod",
    "production": "prod",
    "preprod": "preprod",
    "stage": "staging",
    "staging": "staging",
    "dev": "dev",
    "develop": "dev",
    "prerel": "preprod",
    "prodrel": "prod",
    "relprod": "prod",
    "test": "dev",
    "pretest": "dev",
    "qa": "dev",
}

_SHARED_KEYWORDS = {
    "region-shared-resources",
    "shared",
    "iam",
    "s3",
    "monitoring",
    "monitoring-shared",
    "ops-tools",
    "internal-tools",
    "job-scheduler",
    "ci-runner",
    "dashboards",
    "dashboards-ecs",
    "admin-portal",
    "admin-portal-cross-account",
}


def _match_environment(leaf: str) -> Optional[str]:
    """Return a recognized env if `leaf` matches a known keyword, else None.

    Returning None lets callers distinguish 'no signal' from 'definitely dev',
    which matters when walking up the path looking for a hint.
    """
    lower = leaf.lower()
    if lower in _SHARED_KEYWORDS:
        return "shared"
    # Match longest keyword first so 'preprod' wins over 'prod' etc.
    for keyword in sorted(_ENV_KEYWORDS, key=len, reverse=True):
        if keyword in lower:
            return _ENV_KEYWORDS[keyword]
    # Customer-stack convention: custNN, presetNN, rcN → treat as prod.
    if re.match(r"^cust\d+", lower) or re.match(r"^preset\d*", lower) or re.match(r"^rc\d*$", lower):
        return "prod"
    return None


def suggest_environment(leaf: str) -> str:
    """Default-to-dev wrapper around _match_environment for backwards-compat."""
    return _match_environment(leaf) or "dev"


# Skip noise dirs everywhere we walk — `.terraform` (init cache), `modules`
# (re-used module library — never a standalone stack), plus the usual VCS/IDE
# noise. `.terraform.lock.hcl` lives next to .tf files and isn't itself a dir.
_SKIP_DIRS = {
    ".git",
    ".terraform",
    "modules",
    "node_modules",
    ".github",
    ".idea",
    ".vscode",
}


def _walk_local(root: str) -> Iterable[tuple[str, str]]:
    """Yield (repo-relative dir, kind) for Terraform stacks or Helm charts.

    A directory qualifies if it contains at least one *.tf file (Terraform) or
    a Chart.yaml (Helm). `kind` is "terraform" when any *.tf is present, else
    "helm" when only a Chart.yaml is found. Terraform takes precedence so a
    mixed directory (unusual) is still treated as a Terraform stack.
    """
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        has_tf = any(f.endswith(".tf") for f in files)
        has_chart = "Chart.yaml" in files or "Chart.yml" in files
        if has_tf or has_chart:
            rel = os.path.relpath(current, root)
            if rel == ".":
                continue
            kind = "terraform" if has_tf else "helm"
            yield rel.replace(os.sep, "/"), kind


# Sentinel values used when a repo path doesn't match the AWS-shaped
# `account-XXX/region/...` convention (e.g. a flat `cloudflare/tenant-home-bu/`
# under a multi-provider monorepo). The workspace still gets created with these
# placeholders in its `aws_account_id` / `region` columns so the existing
# schema (NOT NULL on both) keeps working without a migration; the executor's
# AWS-creds-by-account lookup gracefully no-ops when the account isn't in
# `aws_accounts`, which is what we want for non-AWS providers.
GENERIC_ACCOUNT_ID = "global"
GENERIC_REGION = "global"


def _env_hint_from_segments(segments: list[str]) -> str:
    """Pick an environment label by scanning path segments deepest → shallowest
    for a known keyword (dev / staging / prod / …). Falls back to "dev"."""
    for seg in reversed(segments):
        match = _match_environment(seg)
        if match is not None:
            return match
    return "dev"


def _classify(rel_path: str, kind: str = "terraform") -> StackCandidate | None:
    """Map a repo-relative path to a StackCandidate, or None if it doesn't fit.

    AWS convention (preferred): `account-<id>/<region>/<one-or-more-leaf-segments>`.
      - `account-XXX/region/foo`              → name `foo`,    state_key {acc}/{region}/{env}/foo
      - `account-XXX/region/foo/dev`          → name `dev`,    state_key {acc}/{region}/{env}/foo/dev
      - `account-XXX/region/cust01/foo`       → name `foo`,    state_key {acc}/{region}/{env}/cust01/foo

    Generic / non-AWS fallback: any other path that contains `.tf` files
    (e.g. `cloudflare/tenant-home-bu`, `azure/foo`, `dns/zone-com`). These
    get sentinel `aws_account_id="global"` + `region="global"` so the
    schema's NOT NULL constraints are satisfied; the leaf is the last path
    segment and state_key namespaces under `global/global/...` to keep
    AWS and non-AWS state files clearly separated in S3.

    The display `name` is the leaf folder. Uniqueness lives in `tf_working_dir`
    (preserved verbatim) and the S3 state path uses `state_key`, so two
    workspaces with the same leaf but different parents — `cust01/foo` and
    `cust02/foo` — keep their state files apart.
    """
    parts = rel_path.split("/")
    if not parts:
        return None

    # AWS-shaped path: `account-<id>/<region>/<one-or-more-leaf-segments>`.
    if len(parts) >= 3:
        m_acc = ACCOUNT_DIR_RE.match(parts[0])
        region_part = parts[1]
        if m_acc and REGION_DIR_RE.match(region_part):
            leaf_parts = parts[2:]
            # Display label = last segment only. Underscores normalize to
            # hyphens so a `my_stack` directory shows up as `my-stack`.
            leaf_name = leaf_parts[-1].replace("_", "-")
            env_hint = _env_hint_from_segments(leaf_parts)
            state_key = "/".join(
                [m_acc.group(1), region_part, env_hint, *leaf_parts]
            )
            return StackCandidate(
                path=rel_path,
                name=leaf_name,
                aws_account_id=m_acc.group(1),
                region=region_part,
                suggested_environment=env_hint,
                kind=kind,
                state_key=state_key,
            )

    # Reject malformed AWS paths explicitly. A path that *looks* AWS-shaped
    # (`account-<digits>/...`) but fails the strict checks (wrong digit
    # count, bad region) is almost certainly a typo, not a non-AWS module.
    # Silently classifying it as generic would hide the bug; better to skip
    # the candidate and surface nothing.
    if parts[0].startswith("account-"):
        return None
    # Same idea for an obvious AWS region in the wrong place — if a path
    # starts with `us-east-1/...` it's a bare-region misuse, not a flat
    # non-AWS layout.
    if REGION_DIR_RE.match(parts[0]):
        return None
    # And if `parts[1]` is an AWS region, the path was clearly intended to
    # be AWS-shaped (`<something>/us-east-1/foo`). The intent is "AWS but
    # the top-level isn't a valid account dir" — drop it as a typo rather
    # than swallow it into the generic bucket.
    if len(parts) >= 2 and REGION_DIR_RE.match(parts[1]):
        return None

    # Generic / non-AWS fallback (e.g. `cloudflare/tenant-home-bu`,
    # `azure/foo`). Use the whole relative path as the leaf tuple so two
    # stacks with the same leaf name under different provider folders stay
    # uniquely keyed in S3.
    leaf_name = parts[-1].replace("_", "-")
    env_hint = _env_hint_from_segments(parts)
    state_key = "/".join([GENERIC_ACCOUNT_ID, GENERIC_REGION, env_hint, *parts])
    return StackCandidate(
        path=rel_path,
        name=leaf_name,
        aws_account_id=GENERIC_ACCOUNT_ID,
        region=GENERIC_REGION,
        suggested_environment=env_hint,
        kind=kind,
        state_key=state_key,
    )


def discover_local(repo_root: str, repo_url: str = "", ref: str = "main") -> DiscoveryResult:
    """Walk an already-cloned repo on local disk and return a DiscoveryResult.

    Useful for tests (avoids spawning git) and for the dev shortcut where the
    operator has the repo on the terraducktel API container's filesystem.
    """
    stacks: list[StackCandidate] = []
    errors: list[str] = []
    seen: set[str] = set()
    for rel, kind in _walk_local(repo_root):
        c = _classify(rel, kind)
        if c is None:
            continue
        if c.path in seen:
            continue
        seen.add(c.path)
        stacks.append(c)

    by_account: dict[str, DiscoveryAccount] = {}
    for s in sorted(stacks, key=lambda x: (x.aws_account_id, x.region, x.name)):
        acc = by_account.setdefault(s.aws_account_id, DiscoveryAccount(aws_account_id=s.aws_account_id))
        acc.regions.setdefault(s.region, []).append(s)

    return DiscoveryResult(
        repo_url=repo_url,
        ref=ref,
        stacks=stacks,
        accounts=list(by_account.values()),
        errors=errors,
    )


def _inject_credentials(repo_url: str, username: Optional[str], token: Optional[str]) -> str:
    """Inject HTTP Basic creds into a git URL for one-shot clone.

    Username + token are URL-encoded so special characters (e.g. '@', ':') do
    not break parsing. ssh:// URLs are returned unchanged — those need ssh-key
    auth, not embedded creds.
    """
    if not (username and token):
        return repo_url
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.scheme not in ("http", "https"):
        return repo_url
    user_q = urllib.parse.quote(username, safe="")
    tok_q = urllib.parse.quote(token, safe="")
    netloc_no_auth = parsed.netloc.split("@", 1)[-1]
    new_netloc = f"{user_q}:{tok_q}@{netloc_no_auth}"
    return urllib.parse.urlunparse(parsed._replace(netloc=new_netloc))


def _redact_url(url: str) -> str:
    """Strip any embedded creds before logging or returning to the caller."""
    parsed = urllib.parse.urlparse(url)
    if "@" in parsed.netloc:
        host = parsed.netloc.split("@", 1)[-1]
        return urllib.parse.urlunparse(parsed._replace(netloc=host))
    return url


def discover_remote(
    repo_url: str,
    ref: str = "main",
    timeout: int = 60,
    username: Optional[str] = None,
    token: Optional[str] = None,
) -> DiscoveryResult:
    """Shallow-clone a remote repo and run discover_local.

    The clone is automatically deleted before returning. Errors during clone
    are returned in the `errors` list rather than raised so the UI can show
    them inline. Optional username/token are used as one-shot Basic Auth for
    private repos and are NEVER persisted by this function.
    """
    safe_url = _redact_url(repo_url)
    if not repo_url:
        return DiscoveryResult(repo_url="", ref=ref, stacks=[], accounts=[], errors=["repo_url is empty"])
    auth_url = _inject_credentials(repo_url, username, token)
    tmpdir = tempfile.mkdtemp(prefix="terraducktel-discover-")
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", ref, "--", auth_url, tmpdir],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Restrict git to network transports only — blocks ext::/file::
            # command-exec + local-file transports regardless of git version
            #.
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "http:https:ssh",
            },
        )
        if result.returncode != 0:
            # Redact any token that might appear in the error message
            err = (result.stderr or "git clone failed").strip()
            if token:
                err = err.replace(token, "***")
            return DiscoveryResult(
                repo_url=safe_url,
                ref=ref,
                stacks=[],
                accounts=[],
                errors=[err],
            )
        return discover_local(tmpdir, repo_url=safe_url, ref=ref)
    except FileNotFoundError:
        return DiscoveryResult(
            repo_url=safe_url, ref=ref, stacks=[], accounts=[],
            errors=["git binary not available in API container"],
        )
    except subprocess.TimeoutExpired:
        return DiscoveryResult(
            repo_url=safe_url, ref=ref, stacks=[], accounts=[],
            errors=[f"git clone timed out after {timeout}s"],
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def local_repos_root() -> Optional[str]:
    """Return the configured trusted root for local-path discovery, or None."""
    val = os.environ.get("TERRADUCKTEL_LOCAL_REPOS_DIR", "").strip()
    if not val:
        return None
    return os.path.realpath(val)


def discover_local_path(local_path: str) -> DiscoveryResult:
    """Walk a directory mounted into the container.

    Path safety: the resolved real path must live under `TERRADUCKTEL_LOCAL_REPOS_DIR`.
    This prevents an attacker who reaches the discover endpoint from scanning
    arbitrary host filesystem. If the env var is unset, local-path discovery is
    rejected (testing/dev only — production should use git URLs).
    """
    if not local_path:
        return DiscoveryResult(repo_url="", ref="", stacks=[], accounts=[], errors=["local_path is empty"])
    root = local_repos_root()
    if not root:
        return DiscoveryResult(
            repo_url="local://" + local_path, ref="local", stacks=[], accounts=[],
            errors=["local-path discovery is disabled (TERRADUCKTEL_LOCAL_REPOS_DIR not set on the API container)"],
        )
    real = os.path.realpath(local_path)
    if not (real == root or real.startswith(root + os.sep)):
        return DiscoveryResult(
            repo_url="local://" + local_path, ref="local", stacks=[], accounts=[],
            errors=[f"path '{local_path}' is outside TERRADUCKTEL_LOCAL_REPOS_DIR"],
        )
    if not os.path.isdir(real):
        return DiscoveryResult(
            repo_url="local://" + local_path, ref="local", stacks=[], accounts=[],
            errors=[f"path '{local_path}' is not a directory"],
        )
    return discover_local(real, repo_url="local://" + local_path, ref="local")
