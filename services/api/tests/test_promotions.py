"""Integration tests: compare + promotion against a real local bare git repo.

The pipeline itself is not run (no executor in tests) — run statuses are moved
directly in the DB, standing in for the executor's PATCHes, and the promotion
hooks are driven the same way patch_run drives them. Everything git-side is
real: the promotion commit lands in the bare repo and is asserted there.
"""
import os
import subprocess
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.models.audit_log import AuditLog
from app.models.aws_account import AwsAccount
from app.models.business_unit import DEFAULT_BU_ID
from app.models.env_link import EnvPair
from app.models.promotion import Promotion, PromotionRun
from app.models.run import Run, RunStatus
from app.models.user import User
from app.models.workspace import Workspace
from app.services import bg_worker, git_repo, promotion_service, run_service
from app.services import env_compare_service as cmp

pytestmark = pytest.mark.usefixtures("default_bu")

DEV_ACCT, PROD_ACCT = "111111111111", "222222222222"
DEV, PROD = f"account-{DEV_ACCT}", f"account-{PROD_ACCT}"

MAIN_DEV = f'''terraform {{
  required_version = ">= 1.10"
  backend "s3" {{
    bucket = "tf-state-{DEV_ACCT}"
    key    = "app/stack"
  }}
}}

module "app" {{
  source        = "git::https://example.com/mods.git//app?ref=v1.6.2"
  instance_type = "t3.large"
  replicas      = 3
  log_level     = "debug"
}}
'''
MAIN_PROD = f'''terraform {{
  required_version = ">= 1.10"
  backend "s3" {{
    bucket = "tf-state-{PROD_ACCT}"
    key    = "app/stack"
  }}
}}

module "app" {{
  # prod sizing
  source        = "git::https://example.com/mods.git//app?ref=v1.4.0"
  instance_type = "t3.small"
  replicas      = 2
}}
'''
NEWSVC = f'''terraform {{
  backend "s3" {{
    bucket = "tf-state-{DEV_ACCT}"
    key    = "newsvc"
  }}
}}
resource "terraform_data" "x" {{
  input = "arn:aws:iam::{DEV_ACCT}:role/app"
}}
'''

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=GIT_ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A bare repo seeded with a Dev + Prod leaf; returns its file:// URL."""
    monkeypatch.setattr(git_repo, "ALLOWED_PROTOCOLS", "file:http:https:ssh")
    monkeypatch.setattr(git_repo, "FETCH_TTL_SECONDS", 0.0)
    monkeypatch.setattr(git_repo, "_cache_root", lambda: str(tmp_path / "cache"))
    os.makedirs(tmp_path / "cache", exist_ok=True)
    git_repo._last_fetch.clear()
    bare = tmp_path / "remote.git"
    _git("init", "--bare", "-q", "-b", "main", str(bare))
    work = tmp_path / "work"
    _git("clone", "-q", str(bare), str(work))
    for path, text in {
        f"{DEV}/us-east-1/app/stack/main.tf": MAIN_DEV,
        f"{PROD}/us-east-1/app/stack/main.tf": MAIN_PROD,
        f"{DEV}/us-east-1/newsvc/stack/main.tf": NEWSVC,
    }.items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "seed", cwd=work)
    _git("push", "-q", "origin", "HEAD:main", cwd=work)
    return {"url": f"file://{bare}", "bare": str(bare), "work": str(work)}


def _read_remote(remote, path, branch="main"):
    return subprocess.run(["git", "show", f"{branch}:{path}"], cwd=remote["bare"], capture_output=True,
                          text=True).stdout


async def _login(client, email):
    r = await client.post("/api/v1/auth/token", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Business-Unit": "default"}


@pytest_asyncio.fixture
async def env(auth_client, seeded_users, _setup_db, remote, monkeypatch):
    async def no_state(db, side):
        side.state = None

    monkeypatch.setattr(cmp, "read_side_state", no_state)
    async with _setup_db() as s:
        await s.execute(update(User).where(User.email == "admin@test.com").values(is_superadmin=True))
        ws = {}
        for key, path, acct, envname in (
            ("dev", f"{DEV}/us-east-1/app/stack", DEV_ACCT, "dev"),
            ("prod", f"{PROD}/us-east-1/app/stack", PROD_ACCT, "prod"),
            ("newsvc", f"{DEV}/us-east-1/newsvc/stack", DEV_ACCT, "dev"),
        ):
            w = Workspace(id=str(uuid.uuid4()), business_unit_id=DEFAULT_BU_ID, name="stack",
                          aws_account_id=acct, environment=envname, region="us-east-1",
                          repo_url=remote["url"], tf_working_dir=path, repo_ref="main")
            s.add(w)
            ws[key] = w.id
        for acct in (DEV_ACCT, PROD_ACCT):
            s.add(AwsAccount(business_unit_id=DEFAULT_BU_ID, account_id=acct, name=f"acct-{acct}",
                             state_bucket=f"tf-state-{acct}", access_key_id_encrypted="x",
                             secret_access_key_encrypted="x"))
        await s.commit()
    admin = await _login(auth_client, "admin@test.com")
    r = await auth_client.post("/api/v1/env-links", headers=admin, json={
        "name": "dev-to-prod",
        "source_node": {"level": "account", "path": DEV},
        "target_node": {"level": "account", "path": PROD},
    })
    assert r.status_code == 201, r.text
    link = r.json()
    pairs = (await auth_client.get(f"/api/v1/env-links/{link['id']}/pairs", headers=admin)).json()["items"]
    by_path = {p["relative_path"]: p for p in pairs}
    return {"admin": admin, "link": link, "ws": ws, "pairs": by_path, "factory": _setup_db}


async def _enable_git_write(client, admin):
    r = await client.put("/api/v1/integrations/git-write", headers=admin,
                         json={"enabled": True, "token": "test-write-token-123456"})
    assert r.status_code == 200, r.text
    assert r.json()["token_configured"] and r.json()["token_source"] == "dedicated"
    assert "test-write-token" not in r.text  # never echoed


async def _compare(client, env, pair_id, direction="forward"):
    r = await client.post(f"/api/v1/env-links/{env['link']['id']}/compare", headers=env["admin"],
                          json={"pair_ids": [pair_id], "direction": direction})
    assert r.status_code == 202
    await bg_worker.drain(env["factory"])
    r = await client.get(f"/api/v1/env-pairs/{pair_id}/compare?direction={direction}", headers=env["admin"])
    assert r.status_code == 200, r.text
    return r.json()


def _hunk(cfg, key):
    return next(h for h in cfg["config_diff"]["hunks"] if h["key"] == key)


class TestCompare:
    async def test_config_diff_classifies_hunks(self, auth_client, env):
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"])
        cfg = res["config_diff"]
        assert res["refs"]["source"]["commit"] and res["refs"]["target"]["commit"]
        assert _hunk(res, "module.app.source")["classification"] == "promotable"
        assert _hunk(res, "module.app.instance_type")["classification"] == "protected"
        assert _hunk(res, "module.app.replicas")["classification"] == "protected"
        assert _hunk(res, "module.app.log_level")["kind"] == "added"
        backend = [h for h in cfg["hunks"] if h["key"].startswith("terraform.backend")]
        assert backend and all(h["classification"] == "backend" and not h["applicable"] for h in backend)
        assert cfg["summary"]["module_versions"][0]["to"] == "v1.6.2"
        # Comment-only difference (# prod sizing) produced no hunk.
        assert not any("comment" in (h["key"] or "") for h in cfg["hunks"])
        pairs = (await auth_client.get(f"/api/v1/env-links/{env['link']['id']}/pairs", headers=env["admin"])).json()
        assert {p["relative_path"]: p["status"] for p in pairs["items"]}["us-east-1/app/stack"] == "diverged"

    async def test_cached_until_invalidated(self, auth_client, env):
        pair = env["pairs"]["us-east-1/app/stack"]
        await _compare(auth_client, env, pair["id"])
        r = await auth_client.get(f"/api/v1/env-pairs/{pair['id']}/compare", headers=env["admin"])
        assert r.status_code == 200 and r.json()["status"] == "ready"
        # A link rules edit invalidates → next view is 202 (computing) with the stale result.
        await auth_client.put(f"/api/v1/env-links/{env['link']['id']}", headers=env["admin"],
                              json={"protected_rules": {"keys": [], "values": []}})
        r = await auth_client.get(f"/api/v1/env-pairs/{pair['id']}/compare", headers=env["admin"])
        assert r.status_code == 202 and r.json()["stale"] and r.json()["config_diff"] is not None
        await bg_worker.drain(env["factory"])
        r = await auth_client.get(f"/api/v1/env-pairs/{pair['id']}/compare", headers=env["admin"])
        assert r.status_code == 200
        assert _hunk(r.json(), "module.app.instance_type")["classification"] == "promotable"

    async def test_missing_in_target_gets_create_preview(self, auth_client, env):
        pair = env["pairs"]["us-east-1/newsvc/stack"]
        assert pair["status"] == "missing_in_target"
        res = await _compare(auth_client, env, pair["id"])
        prev = res["refs"]["create_preview"]
        assert prev["path"] == f"{PROD}/us-east-1/newsvc/stack"
        text = prev["files"][0]["text"]
        assert DEV_ACCT not in text and PROD_ACCT in text
        assert "backend" not in text
        assert any("backend block removed" in c for c in prev["files"][0]["changes"])


class TestPreview:
    async def test_blockers_without_git_write(self, auth_client, env):
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"])
        body = {"pairs": [{"pair_id": pair["id"], "hunk_ids": [_hunk(res, "module.app.source")["id"]]}]}
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions/preview",
                                   headers=env["admin"], json=body)
        assert r.status_code == 200
        assert any("Git write access is disabled" in b for b in r.json()["blockers"])

    async def test_protected_needs_reason_backend_never(self, auth_client, env):
        await _enable_git_write(auth_client, env["admin"])
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"])
        it = _hunk(res, "module.app.instance_type")["id"]
        be = next(h for h in res["config_diff"]["hunks"] if h["classification"] == "backend")["id"]
        url = f"/api/v1/env-links/{env['link']['id']}/promotions/preview"
        r = await auth_client.post(url, headers=env["admin"], json={"pairs": [{"pair_id": pair["id"], "hunk_ids": [it]}]})
        assert any("protected" in b for b in r.json()["blockers"])
        r = await auth_client.post(url, headers=env["admin"], json={
            "pairs": [{"pair_id": pair["id"], "hunk_ids": [it]}],
            "protected_overrides": [{"hunk_id": it, "reason": "prod needs the bigger box"}],
        })
        assert r.json()["blockers"] == []
        r = await auth_client.post(url, headers=env["admin"], json={"pairs": [{"pair_id": pair["id"], "hunk_ids": [be]}]})
        assert any("never be promoted" in b for b in r.json()["blockers"])

    async def test_reverse_needs_confirmation(self, auth_client, env):
        await _enable_git_write(auth_client, env["admin"])
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"], "reverse")
        h = _hunk(res, "module.app.source")
        assert h["source_value"].endswith('v1.4.0"')
        url = f"/api/v1/env-links/{env['link']['id']}/promotions/preview"
        body = {"direction": "reverse", "pairs": [{"pair_id": pair["id"], "hunk_ids": [h["id"]]}]}
        r = await auth_client.post(url, headers=env["admin"], json=body)
        assert any("confirmed" in b for b in r.json()["blockers"])
        r = await auth_client.post(url, headers=env["admin"], json={**body, "confirm_reverse": True})
        assert r.json()["blockers"] == []
        assert any("reverse" in w for w in r.json()["warnings"])

    async def test_operator_cannot_promote(self, auth_client, env, operator_token):
        h = {"Authorization": f"Bearer {operator_token}"}
        url = f"/api/v1/env-links/{env['link']['id']}/promotions"
        assert (await auth_client.post(url + "/preview", headers=h, json={"pairs": []})).status_code == 403
        assert (await auth_client.post(url, headers=h, json={"pairs": []})).status_code == 403


class TestPromotion:
    async def _promote(self, auth_client, env, keys=("module.app.source", "module.app.log_level")):
        await _enable_git_write(auth_client, env["admin"])
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"])
        body = {"pairs": [{"pair_id": pair["id"], "hunk_ids": [_hunk(res, k)["id"] for k in keys]}]}
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions",
                                   headers=env["admin"], json=body)
        assert r.status_code == 201, r.text
        return r.json(), body

    async def test_commit_push_runs_and_status(self, auth_client, env, remote):
        promo, body = await self._promote(auth_client, env)
        assert promo["status"] == "running" and promo["number"] == 1
        # The commit is on the target's pinned branch, surgical, with trailer + identities.
        text = _read_remote(remote, f"{PROD}/us-east-1/app/stack/main.tf")
        assert 'ref=v1.6.2"' in text and 'log_level     = "debug"' in text or 'log_level = "debug"' in text
        assert 'instance_type = "t3.small"' in text      # protected, not selected
        assert f'bucket = "tf-state-{PROD_ACCT}"' in text  # backend untouched
        assert "# prod sizing" in text                    # formatting/comments preserved
        log = _git("log", "-1", "--format=%an <%ae>|%cn|%B", cwd=remote["bare"])
        assert "admin@test.com" in log and "Terraducktel" in log
        assert f"Terraducktel-Promotion: {promo['id']}" in log
        assert "promote(dev-to-prod)" in log
        # One ordinary apply run on the target stack, never auto-approving.
        async with env["factory"]() as s:
            runs = (await s.execute(select(Run).where(Run.promotion_id == promo["id"]))).scalars().all()
        assert len(runs) == 1
        run = runs[0]
        assert run.workspace_id == env["ws"]["prod"] and run.command == "apply"
        assert not run.auto_approve_if_no_changes and not run.auto_approve_skip_apply
        assert run.status == RunStatus.PENDING

        # Double click → same promotion, no second commit.
        head = _git("rev-parse", "main", cwd=remote["bare"])
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions", headers=env["admin"], json=body)
        assert r.status_code == 200 and r.json()["id"] == promo["id"] and r.json()["replayed"]
        assert _git("rev-parse", "main", cwd=remote["bare"]) == head

        # Another promotion on the same target stack is blocked while this one is active.
        pv = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions/preview",
                                    headers=env["admin"], json=body)
        assert any("still active" in b or "run in progress" in b for b in pv.json()["blockers"])

        # Executor stand-in: plan → approval → apply → applied.
        async with env["factory"]() as s:
            r_ = await s.get(Run, run.id)
            for st in (RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL):
                r_.transition(st)
            await s.commit()
        d = (await auth_client.get(f"/api/v1/promotions/{promo['id']}", headers=env["admin"])).json()
        assert d["status"] == "awaiting_approval"
        assert [s_["key"] for s_ in d["stacks"][0]["stages"]] == [
            "committed", "checkov", "plan", "opa", "cost", "approval", "apply", "verified"]
        async with env["factory"]() as s:
            r_ = await s.get(Run, run.id)
            r_.transition(RunStatus.APPLYING)
            r_.transition(RunStatus.APPLIED)
            await s.commit()
            await promotion_service.on_run_changed(s, await s.get(Run, run.id))
        await bg_worker.drain(env["factory"])
        d = (await auth_client.get(f"/api/v1/promotions/{promo['id']}", headers=env["admin"])).json()
        assert d["status"] == "succeeded"
        res = d["stacks"][0]["residual"]
        # Only protected (not selected) differences remain → in sync for promotion.
        assert res["in_sync"] is True and res["promotable"] == 0

        # Audit trail.
        async with env["factory"]() as s:
            actions = [a.action for a in (await s.execute(
                select(AuditLog).where(AuditLog.resource_type == "promotion").order_by(AuditLog.created_at)
            )).scalars()]
        assert actions[0] == "promotion.create" and "promotion.status" in actions

    async def test_rejected_run_derives_rejected(self, auth_client, env):
        promo, body = await self._promote(auth_client, env)
        async with env["factory"]() as s:
            run = (await s.execute(select(Run).where(Run.promotion_id == promo["id"]))).scalar_one()
            run.transition(RunStatus.RUNNING)
            run.transition(RunStatus.AWAITING_APPROVAL)
            run.transition(RunStatus.CANCELLED)
            await s.commit()
        d = (await auth_client.get(f"/api/v1/promotions/{promo['id']}", headers=env["admin"])).json()
        assert d["status"] == "rejected"
        # Re-posting the same selection after a rejection is NOT an idempotent
        # replay of the rejected promotion: it is re-validated — and since the
        # rejected promotion's commit is already on the branch, it is blocked.
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions",
                                   headers=env["admin"], json=body)
        assert r.status_code == 409 and r.json()["detail"]["blockers"]

    async def test_non_fast_forward_retries_once(self, auth_client, env, remote, monkeypatch):
        await _enable_git_write(auth_client, env["admin"])
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"])
        real = git_repo.commit_files
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # Someone pushes an unrelated change to the target branch meanwhile.
                w = remote["work"]
                _git("pull", "-q", "origin", "main", cwd=w)
                open(os.path.join(w, "README.md"), "w").write("hi\n")
                _git("add", "-A", cwd=w)
                _git("commit", "-q", "-m", "unrelated", cwd=w)
                _git("push", "-q", "origin", "HEAD:main", cwd=w)
                raise git_repo.NonFastForward("moved")
            return real(*a, **kw)

        monkeypatch.setattr(git_repo, "commit_files", flaky)
        body = {"pairs": [{"pair_id": pair["id"], "hunk_ids": [_hunk(res, "module.app.source")["id"]]}]}
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions", headers=env["admin"], json=body)
        assert r.status_code == 201, r.text
        assert calls["n"] == 2 and r.json()["status"] == "running"
        assert _read_remote(remote, "README.md") == "hi\n"
        assert 'ref=v1.6.2"' in _read_remote(remote, f"{PROD}/us-east-1/app/stack/main.tf")

    async def test_push_rejected_marks_commit_failed(self, auth_client, env, monkeypatch):
        await _enable_git_write(auth_client, env["admin"])
        pair = env["pairs"]["us-east-1/app/stack"]
        res = await _compare(auth_client, env, pair["id"])

        def rejected(*a, **kw):
            raise git_repo.PushRejected("The target branch is protected and requires a pull request")

        monkeypatch.setattr(git_repo, "commit_files", rejected)
        body = {"pairs": [{"pair_id": pair["id"], "hunk_ids": [_hunk(res, "module.app.source")["id"]]}]}
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions", headers=env["admin"], json=body)
        assert r.status_code == 201
        assert r.json()["status"] == "commit_failed" and "protected" in r.json()["error"]
        async with env["factory"]() as s:
            assert (await s.execute(select(Run).where(Run.promotion_id == r.json()["id"]))).first() is None

    async def test_create_in_target(self, auth_client, env, remote):
        await _enable_git_write(auth_client, env["admin"])
        pair = env["pairs"]["us-east-1/newsvc/stack"]
        await _compare(auth_client, env, pair["id"])
        body = {"pairs": [{"pair_id": pair["id"], "create_in_target": True}]}
        r = await auth_client.post(f"/api/v1/env-links/{env['link']['id']}/promotions", headers=env["admin"], json=body)
        assert r.status_code == 201, r.text
        text = _read_remote(remote, f"{PROD}/us-east-1/newsvc/stack/main.tf")
        assert f"arn:aws:iam::{PROD_ACCT}:role/app" in text and "backend" not in text
        async with env["factory"]() as s:
            ws = (await s.execute(select(Workspace).where(
                Workspace.tf_working_dir == f"{PROD}/us-east-1/newsvc/stack"))).scalar_one()
            assert ws.business_unit_id == DEFAULT_BU_ID and ws.aws_account_id == PROD_ACCT
            assert ws.repo_ref == "main" and ws.state_key
            run = (await s.execute(select(Run).where(Run.promotion_id == r.json()["id"]))).scalar_one()
            assert run.workspace_id == ws.id and run.command == "apply"
        pairs = (await auth_client.get(f"/api/v1/env-links/{env['link']['id']}/pairs", headers=env["admin"])).json()
        assert {p["relative_path"]: p["status"] for p in pairs["items"]}["us-east-1/newsvc/stack"] == "not_compared"

    async def test_revert(self, auth_client, env, remote):
        promo, _ = await self._promote(auth_client, env)
        # Can't revert while it's still active.
        r = await auth_client.post(f"/api/v1/promotions/{promo['id']}/revert", headers=env["admin"])
        assert r.status_code == 409
        async with env["factory"]() as s:
            run = (await s.execute(select(Run).where(Run.promotion_id == promo["id"]))).scalar_one()
            for st in (RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL, RunStatus.APPLYING, RunStatus.APPLIED):
                run.transition(st)
            await s.commit()
        r = await auth_client.post(f"/api/v1/promotions/{promo['id']}/revert", headers=env["admin"])
        assert r.status_code == 201, r.text
        rev = r.json()
        assert rev["kind"] == "revert" and rev["status"] == "running"
        text = _read_remote(remote, f"{PROD}/us-east-1/app/stack/main.tf")
        assert text == MAIN_PROD
        async with env["factory"]() as s:
            rrun = (await s.execute(select(Run).where(Run.promotion_id == rev["id"]))).scalar_one()
            assert rrun.command == "apply" and not rrun.auto_approve_if_no_changes
        again = await auth_client.post(f"/api/v1/promotions/{promo['id']}/revert", headers=env["admin"])
        assert again.status_code == 409


class TestGuards:
    async def test_run_service_refuses_auto_approve_for_promotions(self, _setup_db, default_bu):
        async with _setup_db() as s:
            ws = Workspace(id=str(uuid.uuid4()), business_unit_id=DEFAULT_BU_ID, name="x", aws_account_id=DEV_ACCT,
                           environment="dev", region="us-east-1", tf_working_dir="a", repo_ref="main")
            s.add(ws)
            await s.flush()
            with pytest.raises(ValueError, match="never auto-approve"):
                await run_service.create_run(s, ws, command="apply", triggered_by="u",
                                             auto_approve_if_no_changes=True, promotion_id="p")

    async def test_webhook_skips_promotion_only_pushes(self):
        from app.routers.webhooks import _changed_files, _promotion_only_push

        promo = {"message": "promote(x): a → b\n\nTerraducktel-Promotion: 123", "modified": ["a/main.tf"]}
        human = {"message": "fix", "modified": ["b/main.tf"]}
        assert _promotion_only_push({"commits": [promo]})
        assert not _promotion_only_push({"commits": [promo, human]})
        assert _changed_files({"commits": [promo, human]}, skip_promotions=True) == {"b/main.tf"}

    async def test_git_write_is_per_bu_and_off_by_default(self, auth_client, env, _setup_db):
        r = await auth_client.get("/api/v1/integrations/git-write", headers=env["admin"])
        assert r.json()["enabled"] is False
        # A global github.token must NOT be picked up as the write credential.
        from app.auth.encryption_key import get_credential_encryption_key
        from app.services.config_service import ConfigService

        async with _setup_db() as s:
            await ConfigService(s, get_credential_encryption_key()).set("github.token", "global-token", is_secret=True)
            await s.commit()
        r = await auth_client.get("/api/v1/integrations/git-write", headers=env["admin"])
        assert r.json()["token_configured"] is False
