"""Regression tests for the executor's step-timeline PATCH payload.

A `terraform plan/apply` log on a real stack is megabytes. The step helper used
to hand that log to `jq` as a command-line argument (`jq --arg o "$output"`),
and execve caps a *single* argument at MAX_ARG_STRLEN — 128 KB on Linux,
regardless of ARG_MAX. Past that, jq never starts:

    /entrypoint.sh: line 121: /usr/bin/jq: Argument list too long

Worse than the lost timeline row: the failure happens inside a shell function,
and bash skips the ERR trap for failures inside functions unless errtrace
(`set -E`) is on — it isn't. So `set -e` killed the executor at exit 126 with
`on_unexpected_err` never firing, nothing was reported, and the run sat in
`running` until the API reaper failed it `worker.stale_after_seconds` later
with "executor died before reporting any step status" (see run_worker.py).
Runs under the limit were unaffected, so this only ever bit big workspaces.

These tests pin the fix: payloads go through files (`--rawfile`), and the
executor reports its own unexpected death instead of leaving it to the reaper.
"""
import os
import re
import shutil
import subprocess

import pytest

ENTRYPOINT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../executor/entrypoint.sh")
)

# The kernel's per-argument ceiling (32 pages). The point of the fix is that
# step output an order of magnitude past this still goes through.
MAX_ARG_STRLEN = 128 * 1024


def entrypoint_source() -> str:
    with open(ENTRYPOINT) as f:
        return f.read()


def bash_function(name: str) -> str:
    """Extract one top-level `name() { ... }` block from the entrypoint."""
    src = entrypoint_source()
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}$", src, re.S | re.M)
    assert m, f"{name}() not found in {ENTRYPOINT}"
    return m.group(0)


def code_only(shell: str) -> str:
    """Drop comment lines — the comments here quote the very anti-pattern the
    static guards below search for."""
    return "\n".join(
        line for line in shell.splitlines() if not line.lstrip().startswith("#")
    )


# ── Static guards ──────────────────────────────────────────────────────────


def test_step_never_passes_output_or_summary_via_argv():
    body = code_only(bash_function("step"))
    assert '--arg o ' not in body, (
        "step() must not pass $output as a jq command-line argument — "
        f"execve caps one argument at {MAX_ARG_STRLEN} bytes and a real "
        "plan/apply log is far bigger. Use --rawfile."
    )
    assert '--arg j ' not in body, "step() must not pass $summary via argv either"
    assert "--rawfile o " in body and "--data-binary @" in body


def test_stream_step_output_never_passes_output_via_argv():
    body = code_only(bash_function("stream_step_output"))
    assert '--arg o ' not in body, (
        "the live-output streamer must not pass the log tail via argv; the "
        "tail cap is not a safe place to rely on"
    )
    assert "--rawfile o " in body and "--data-binary @" in body


def test_opa_summary_findings_do_not_go_through_argv():
    src = code_only(entrypoint_source())
    assert '--argjson failures ' not in src, (
        "OPA findings arrays can exceed MAX_ARG_STRLEN on a large plan; "
        "pass them with --slurpfile"
    )
    assert "--slurpfile failures " in src


def test_executor_reports_its_own_unexpected_death():
    """The reaper is the backstop, not the error channel."""
    src = entrypoint_source()
    assert "trap on_exit EXIT" in src, (
        "an EXIT trap is the only hook that survives `set -e` firing inside a "
        "function (bash skips ERR traps there without errtrace)"
    )
    body = bash_function("on_exit")
    assert "TERMINAL_STATUS_REPORTED" in body, (
        "on_exit must not relabel a run that already reported a terminal status"
    )
    assert "report_status" in body
    # SIGINT/SIGTERM = an operator cancelling the run; that run is already
    # `cancelled` and must not be relabelled `failed`.
    assert "130" in body and "143" in body


# ── Functional: drive the real step() with an oversized payload ────────────


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="needs bash + jq (both are in the executor image)",
)
@pytest.mark.parametrize("summary", ["", '{"add":139,"change":0,"destroy":0}'])
def test_step_survives_a_payload_far_past_the_argv_limit(tmp_path, summary):
    """1 MB of step output must produce a well-formed PATCH body, not exit 126.

    Runs the *real* `step()` lifted out of entrypoint.sh under the same
    `set -euo pipefail` the executor uses, with curl stubbed out so we can
    inspect the body it would have sent.
    """
    payload_len = 1024 * 1024
    assert payload_len > MAX_ARG_STRLEN

    harness = tmp_path / "harness.sh"
    captured = tmp_path / "body.json"
    harness.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'API_URL="http://stub"; API_TOKEN="t"; RUN_ID="r"\n'
        # Stub curl: keep the body file the real step() built.
        "curl() {\n"
        '  local a; for a in "$@"; do\n'
        '    case "$a" in --data-binary@*|--data-binary) ;; @*) cp "${a#@}" '
        f'"{captured}" ;;\n'
        "    esac\n"
        "  done\n"
        "  return 0\n"
        "}\n"
        # Stub the steps lookup so step() finds an id without an API.
        'step_id_for() { echo "step-1"; }\n'
        f"{bash_function('step')}\n"
        f'output=$(head -c {payload_len} /dev/zero | tr "\\0" "x")\n'
        f'step "Terraform Plan" success "$output" {summary!r}\n'
        'echo "SURVIVED"\n'
    )

    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"step() died (rc={proc.returncode}) on a {payload_len}-byte output.\n"
        f"stderr: {proc.stderr[:2000]}"
    )
    assert "SURVIVED" in proc.stdout
    # 126 is bash's "found it but could not execute it" — the old symptom.
    assert "Argument list too long" not in proc.stderr

    import json

    body = json.loads(captured.read_text())
    assert body["status"] == "success"
    assert len(body["output"]) == payload_len, "the full log must reach the API"
    if summary:
        assert body["summary_json"] == summary
    else:
        # Unchanged from before the fix: the no-summary branch omits the key
        # rather than sending an explicit null.
        assert "summary_json" not in body


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="needs bash + jq",
)
def test_the_old_argv_form_really_did_die_silently(tmp_path):
    """Characterise the bug so the fix above can't be undone as cosmetic.

    Proves both halves of the production failure: bash aborts with 126, and
    the ERR trap does NOT fire because the failure is inside a function.
    """
    harness = tmp_path / "old.sh"
    harness.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "trap 'echo ERR_TRAP_FIRED' ERR\n"
        "old_step() {\n"
        '  local output="$1"\n'
        "  local body\n"
        "  body=$(jq -n --arg o \"$output\" '{output:$o}')\n"
        '  echo "REACHED_CURL"\n'
        "}\n"
        f'big=$(head -c {MAX_ARG_STRLEN + 4096} /dev/zero | tr "\\0" "x")\n'
        'old_step "$big"\n'
        "echo REACHED_END\n"
    )
    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 126, f"expected exit 126, got {proc.returncode}"
    assert "Argument list too long" in proc.stderr
    assert "REACHED_CURL" not in proc.stdout
    assert "ERR_TRAP_FIRED" not in proc.stdout, (
        "if bash ever starts firing ERR traps inside functions without "
        "errtrace, on_unexpected_err would have caught this in production"
    )


# ── Functional: the EXIT-trap backstop ────────────────────────────────────


def _exit_trap_harness(tmp_path, api_status: str, boom: str) -> tuple:
    """Build a harness that runs the real report_status() + on_exit() with a
    stubbed API, then dies the way production died: nonzero inside a function,
    where bash skips the ERR trap.
    """
    recorded = tmp_path / "patched.json"
    harness = tmp_path / "exit.sh"
    harness.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'API_URL="http://stub"; API_TOKEN="t"; RUN_ID="r"\n'
        "curl() {\n"
        '  local a is_patch=0 body=""\n'
        '  for a in "$@"; do\n'
        '    [[ "$a" == "PATCH" ]] && is_patch=1\n'
        '    case "$a" in @*) body="${a#@}" ;; esac\n'
        "  done\n"
        '  if [[ "$is_patch" == "1" ]]; then\n'
        f'    [[ -n "$body" ]] && cp "$body" "{recorded}"\n'
        "  else\n"
        f"    printf '%s' '{{\"status\":\"{api_status}\"}}'\n"
        "  fi\n"
        "  return 0\n"
        "}\n"
        f"{bash_function('report_status')}\n"
        f"{bash_function('on_exit')}\n"
        "TERMINAL_STATUS_REPORTED=0\n"
        "HEARTBEAT_PID=\n"
        "trap on_exit EXIT\n"
        # The production shape: `set -e` fires inside a function, so the ERR
        # trap never runs and on_unexpected_err never reports.
        f"{boom}\n"
        "boom\n"
        "echo REACHED_END\n"
    )
    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, timeout=60
    )
    return proc, recorded


BOOM = 'boom() {\n  local x\n  x=$(exit 7)\n  echo NOT_REACHED\n}'


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="needs bash + jq",
)
def test_exit_trap_reports_a_silent_death_instead_of_waiting_for_the_reaper(
    tmp_path,
):
    proc, recorded = _exit_trap_harness(tmp_path, "running", BOOM)

    assert "REACHED_END" not in proc.stdout, "the harness must really die"
    assert recorded.exists(), (
        "on_exit must PATCH the run failed; without it the run sits in "
        "`running` until the reaper times it out"
    )
    import json

    body = json.loads(recorded.read_text())
    assert body["status"] == "failed"
    assert "exited unexpectedly (exit=7)" in body["plan_output"]


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="needs bash + jq",
)
@pytest.mark.parametrize("api_status", ["awaiting_approval", "planned", "applied"])
def test_exit_trap_never_relabels_a_run_the_api_already_finished(
    tmp_path, api_status
):
    """The plan phase writes `planned` / `awaiting_approval` with its own curl
    (those bodies carry plan_json + the tfplan blob), so the local flag alone
    can't be trusted — on_exit must ask the API before overwriting."""
    _proc, recorded = _exit_trap_harness(tmp_path, api_status, BOOM)
    assert not recorded.exists(), (
        f"on_exit relabelled a run already at {api_status!r} as failed"
    )


# ── The other half of the contract: the API accepts what we now send ──────


@pytest.mark.asyncio
async def test_api_stores_a_step_output_far_past_the_argv_limit(
    auth_client, seeded_users, default_aws_account, _setup_db
):
    """Fixing the executor is only half of it — the API has to take the body.

    Pins that there is no request-size cap in the router and no truncation on
    the way to the column, so a future body-size limit can't silently
    reintroduce the wedge.
    """
    import uuid

    from app.models.business_unit import DEFAULT_BU_ID
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            Workspace(
                business_unit_id=DEFAULT_BU_ID,
                id=ws_id,
                name="bigplan",
                aws_account_id="123456789012",
                region="us-east-1",
                environment="dev",
                tf_working_dir=".",
                repo_url="https://example.com/x.git",
            )
        )
        await session.commit()

    token = (
        await auth_client.post(
            "/api/v1/auth/token",
            json={"email": "operator@test.com", "password": "password123"},
        )
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    run_id = (
        await auth_client.post(
            f"/api/v1/workspaces/{ws_id}/runs", json={"command": "plan"}, headers=auth
        )
    ).json()["id"]
    steps = (
        await auth_client.get(f"/api/v1/runs/{run_id}/steps", headers=auth)
    ).json()
    step_id = steps[0]["id"]

    # 2 MB — 16x the old argv cliff, the order of magnitude a 139-resource
    # plan actually produces.
    output = "x" * (2 * 1024 * 1024)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}/steps/{step_id}",
        json={"status": "success", "output": output, "summary_json": '{"add":139}'},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["output"]) == len(output)

    # And it survives the round trip out of the column.
    back = await auth_client.get(f"/api/v1/runs/{run_id}/steps", headers=auth)
    stored = next(s for s in back.json() if s["id"] == step_id)
    assert len(stored["output"]) == len(output)
    assert stored["summary_json"] == '{"add":139}'
