"""Coverage for cluster_service + the /api/v1/clusters router, including the
kubectl-backed connectivity test (subprocess mocked, no real cluster)."""
import subprocess

import pytest

from app.routers import clusters as crouter
from app.services import cluster_service as cs
from app.models.k8s_cluster import K8sCluster
from app.models.business_unit import DEFAULT_BU_ID

pytestmark = pytest.mark.usefixtures("default_aws_account")

_KUBECONFIG = "apiVersion: v1\nkind: Config\nclusters: []\n"


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


# ─── cluster_service ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("plain,expected", [("", ""), ("abcdefghij", "…efghij"), ("ab", "***")])
def test_mask_tail(plain, expected):
    assert cs.mask_tail(plain) == expected


def _kubeconfig_with_exec(command: str) -> str:
    return (
        "apiVersion: v1\nkind: Config\nusers:\n"
        "- name: u\n  user:\n    exec:\n"
        "      apiVersion: client.authentication.k8s.io/v1beta1\n"
        f"      command: {command}\n"
    )


def test_validate_kubeconfig_allows_known_cloud_helper():
    # EKS's `aws eks get-token` exec plugin is legitimate.
    cs.validate_kubeconfig(_kubeconfig_with_exec("aws"))
    cs.validate_kubeconfig(_KUBECONFIG)  # no exec at all


@pytest.mark.parametrize(
    "command",
    [
        "/bin/sh",            # arbitrary interpreter
        "./aws",              # relative path → planted binary in cloned repo ( bypass)
        "/tmp/evil/aws",      # absolute path whose basename spoofs an allowed helper
        "bash",               # not a cloud auth helper
        "curl",
    ],
)
def test_validate_kubeconfig_rejects_unsafe_exec(command):
    with pytest.raises(cs.UnsafeKubeconfigError):
        cs.validate_kubeconfig(_kubeconfig_with_exec(command))


async def test_cluster_service_crud(db_session):
    c = await cs.create_cluster(
        db_session,
        business_unit_id=DEFAULT_BU_ID,
        name="eks-1",
        kubeconfig=_KUBECONFIG,
        aws_account_id="123456789012",
    )
    assert cs.decrypt_secret(c.kubeconfig_encrypted) == _KUBECONFIG
    assert [x.id for x in await cs.list_clusters(db_session)] == [c.id]
    assert len(await cs.list_clusters(db_session, DEFAULT_BU_ID)) == 1
    assert (await cs.get_cluster(db_session, c.id, DEFAULT_BU_ID)).id == c.id
    assert await cs.get_cluster(db_session, c.id, "other") is None

    # update: change every optional field + re-encrypt kubeconfig + clear aws acct
    await cs.update_cluster(
        db_session, c, name="eks-2", description="d", server_url="https://k8s",
        default_namespace="ops", kubeconfig="new-config", aws_account_id=None,
        aws_account_id_set=True,
    )
    assert c.name == "eks-2" and c.aws_account_id is None
    assert c.description == "d" and c.server_url == "https://k8s" and c.default_namespace == "ops"
    assert cs.decrypt_secret(c.kubeconfig_encrypted) == "new-config"

    # run-time kubeconfig fetch
    assert await cs.get_cluster_kubeconfig(db_session, c.id) == "new-config"
    assert await cs.get_cluster_kubeconfig(db_session, "missing") is None

    await cs.delete_cluster(db_session, c)
    assert await cs.get_cluster(db_session, c.id) is None


# ─── router CRUD ─────────────────────────────────────────────────────────────


async def _create(client, token, **over):
    body = {"name": "k", "kubeconfig": _KUBECONFIG}
    body.update(over)
    r = await client.post("/api/v1/clusters", json=body, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


async def test_router_crud(auth_client, admin_token):
    c = await _create(auth_client, admin_token, name="prod-eks")
    assert c["kubeconfig_tail"].startswith("…")
    lst = await auth_client.get("/api/v1/clusters", headers=_h(admin_token))
    assert any(x["id"] == c["id"] for x in lst.json())
    upd = await auth_client.put(
        f"/api/v1/clusters/{c['id']}",
        json={"name": "renamed", "kubeconfig": "k2", "aws_account_id": "123456789012"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "renamed"
    d = await auth_client.delete(f"/api/v1/clusters/{c['id']}", headers=_h(admin_token))
    assert d.status_code == 204


async def test_router_404s_and_bu_guard(auth_client, admin_token, _setup_db):
    assert (
        await auth_client.put("/api/v1/clusters/x", json={"name": "n"}, headers=_h(admin_token))
    ).status_code == 404
    assert (
        await auth_client.delete("/api/v1/clusters/x", headers=_h(admin_token))
    ).status_code == 404
    # superadmin + 'all' → no concrete BU → 400
    from app.models.user import User
    from sqlalchemy import select

    async with _setup_db() as s:
        u = (await s.execute(select(User).where(User.email == "admin@test.com"))).scalars().first()
        u.is_superadmin = True
        await s.commit()
    r = await auth_client.post(
        "/api/v1/clusters", json={"name": "k", "kubeconfig": _KUBECONFIG},
        headers=_h(admin_token, bu="all"),
    )
    assert r.status_code == 400


async def test_router_list_unreadable(auth_client, admin_token, _setup_db):
    c = await _create(auth_client, admin_token, name="corrupt")
    async with _setup_db() as s:
        row = await s.get(K8sCluster, c["id"])
        row.kubeconfig_encrypted = "bad"
        await s.commit()
    lst = await auth_client.get("/api/v1/clusters", headers=_h(admin_token))
    assert next(x for x in lst.json() if x["id"] == c["id"])["kubeconfig_tail"] == "(unreadable)"


# ─── test-connection endpoint (subprocess mocked) ────────────────────────────


def _cp(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def _install_run(monkeypatch, *, version_rc=0, version_stderr="", version_exc=None, ctx_exc=False):
    def fake_run(cmd, **kw):
        if "version" in cmd:
            if version_exc:
                raise version_exc
            return _cp(cmd, version_rc, stdout="{}", stderr=version_stderr)
        # current-context
        if ctx_exc:
            raise RuntimeError("ctx boom")
        return _cp(cmd, 0, stdout="my-context\n")

    monkeypatch.setattr(crouter.subprocess, "run", fake_run)


async def test_connectivity_success_with_context_and_eks(auth_client, admin_token, monkeypatch):
    # aws_account_id set → exercises the EKS credential-export branch.
    c = await _create(auth_client, admin_token, name="eks", aws_account_id="123456789012")
    _install_run(monkeypatch, version_rc=0)
    r = await auth_client.post(f"/api/v1/clusters/{c['id']}/test", headers=_h(admin_token))
    assert r.json()["ok"] is True and r.json()["context"] == "my-context"


async def test_connectivity_nonzero_and_ctx_exception(auth_client, admin_token, monkeypatch):
    c = await _create(auth_client, admin_token, name="bad")
    _install_run(monkeypatch, version_rc=1, version_stderr="connection refused", ctx_exc=True)
    r = await auth_client.post(f"/api/v1/clusters/{c['id']}/test", headers=_h(admin_token))
    assert r.json()["ok"] is False and "connection refused" in r.json()["detail"]


async def test_connectivity_kubectl_missing(auth_client, admin_token, monkeypatch):
    c = await _create(auth_client, admin_token, name="nokubectl")
    _install_run(monkeypatch, version_exc=FileNotFoundError())
    r = await auth_client.post(f"/api/v1/clusters/{c['id']}/test", headers=_h(admin_token))
    assert "kubectl is not installed" in r.json()["detail"]


async def test_connectivity_timeout(auth_client, admin_token, monkeypatch):
    c = await _create(auth_client, admin_token, name="slow")
    _install_run(monkeypatch, version_exc=subprocess.TimeoutExpired(cmd="kubectl", timeout=15))
    r = await auth_client.post(f"/api/v1/clusters/{c['id']}/test", headers=_h(admin_token))
    assert "timed out" in r.json()["detail"]


async def test_connectivity_decrypt_failure(auth_client, admin_token, _setup_db):
    c = await _create(auth_client, admin_token, name="corruptcfg")
    async with _setup_db() as s:
        row = await s.get(K8sCluster, c["id"])
        row.kubeconfig_encrypted = "bad"
        await s.commit()
    r = await auth_client.post(f"/api/v1/clusters/{c['id']}/test", headers=_h(admin_token))
    assert r.json()["ok"] is False and "could not be decrypted" in r.json()["detail"]


async def test_connectivity_404(auth_client, admin_token):
    r = await auth_client.post("/api/v1/clusters/missing/test", headers=_h(admin_token))
    assert r.status_code == 404


async def test_connectivity_outer_except_and_remove_oserror(auth_client, admin_token, monkeypatch):
    c = await _create(auth_client, admin_token, name="boom")
    # os.write raises → outer except (sanitized). os.remove raises OSError →
    # swallowed by the finally block.
    monkeypatch.setattr(crouter.os, "write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))

    def bad_remove(p):
        raise OSError("locked")

    monkeypatch.setattr(crouter.os, "remove", bad_remove)
    r = await auth_client.post(f"/api/v1/clusters/{c['id']}/test", headers=_h(admin_token))
    assert r.json()["ok"] is False
