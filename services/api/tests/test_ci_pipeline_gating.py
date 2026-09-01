"""The deploy job must stay gated behind the test jobs.

For a long time it wasn't. `ci.yml`, `release.yml` and `deploy-dev.yml` all
fired on `push: dev` and ran concurrently — `needs:` cannot cross workflow
files — so `terraform apply` rolled production onto a new image while the test
suite was still running. Feature PRs targeted `dev`, which `ci.yml` did not
list under `pull_request`, so they ran no checks at all before merging into
that auto-deploy.

Merging the three into one workflow fixed it. These tests keep it fixed: they
fail if the dependency chain is cut, if `dev` drops out of the PR triggers, or
if a second workflow starts deploying on the side.

Deploying is deployment-specific, so a fork may legitimately ship this pipeline
with only its test/release half. The deploy-specific assertions below skip when
there is no deploy job, which keeps this one file valid in both repos rather
than forking it — a forked guard is a guard that drifts.
"""
from pathlib import Path

import pytest
import yaml  # declared in the `dev` extra — a skip here would hide a regression

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
PIPELINE = WORKFLOWS / "ci-cd.yml"

TEST_JOBS = {"api-tests", "cli-tests", "ui", "release-version"}


@pytest.fixture(scope="module")
def wf() -> dict:
    assert PIPELINE.exists(), f"pipeline missing at {PIPELINE}"
    return yaml.safe_load(PIPELINE.read_text())


def _needs(job: dict) -> list[str]:
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def _ancestors(wf: dict, name: str) -> set[str]:
    """Every job `name` transitively depends on."""
    seen: set[str] = set()
    stack = list(_needs(wf["jobs"][name]))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(_needs(wf["jobs"][cur]))
    return seen


def _require_deploy_half(wf: dict) -> None:
    missing = {"meta", "build", "deploy"} - set(wf["jobs"])
    if missing:
        pytest.skip(f"pipeline has no deploy half (missing {sorted(missing)})")


def test_deploy_transitively_depends_on_every_test_job(wf):
    _require_deploy_half(wf)
    missing = TEST_JOBS - _ancestors(wf, "deploy")
    assert not missing, (
        f"deploy no longer waits for: {sorted(missing)}. "
        "A failing suite would be discovered after production had rolled."
    )


def test_build_also_waits_for_the_tests(wf):
    """Fail before burning five parallel image builds, not after."""
    _require_deploy_half(wf)
    assert TEST_JOBS <= _ancestors(wf, "build")


def test_deploy_only_runs_on_a_push_to_dev(wf):
    _require_deploy_half(wf)
    cond = " ".join(str(wf["jobs"]["meta"].get("if", "")).split())
    assert "github.event_name == 'push'" in cond
    assert "github.ref == 'refs/heads/dev'" in cond


def test_pull_requests_into_dev_are_checked(wf):
    """`dev` is the default branch; every feature PR targets it."""
    branches = (wf[True] if True in wf else wf["on"])["pull_request"]["branches"]
    assert "dev" in branches, f"PRs into dev run no checks (branches={branches})"


def test_release_runs_before_the_image_tag_is_computed(wf):
    """Ordering, not gating: `meta` reads `git tag -l`, and the release job is
    what creates this commit's tag. Concurrently, a version-bumping commit
    produced an image named after the *previous* release."""
    _require_deploy_half(wf)
    assert "release" in _needs(wf["jobs"]["meta"])


def test_advisory_scan_is_not_a_dependency(wf):
    """`security-scan` is continue-on-error, so it cannot fail the workflow —
    but a job depending on it would still be SKIPPED when it fails, quietly
    turning an advisory scan into a deploy blocker. If it should gate, drop
    continue-on-error first."""
    assert wf["jobs"]["security-scan"].get("continue-on-error") is True
    for name, job in wf["jobs"].items():
        assert "security-scan" not in _needs(job), (
            f"{name} depends on the advisory scan — see this test's docstring"
        )


def test_no_second_workflow_deploys_on_the_side(wf):
    """The original bug in one line: a separate file with its own push trigger."""
    others = [p for p in WORKFLOWS.glob("*.yml") if p != PIPELINE]
    for path in others:
        doc = yaml.safe_load(path.read_text()) or {}
        blob = str(doc)
        assert "terraform apply" not in blob and "configure-aws-credentials" not in blob, (
            f"{path.name} deploys independently of the gated pipeline"
        )


def test_pushes_to_dev_are_never_cancelled_mid_deploy(wf):
    """Interrupting a terraform apply or an ECS roll is worse than queueing.

    Also protects the release half on a deploy-less fork: two rapid merges must
    not race on creating the same tag."""
    cancel = str(wf["concurrency"]["cancel-in-progress"])
    assert "pull_request" in cancel, (
        "cancel-in-progress must be limited to PR runs; "
        f"got {cancel!r}"
    )
