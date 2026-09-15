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


async def test_update_clears_ca_cert_pem(auth_client, admin_token):
    """Test that PUT with ca_cert_pem: "" clears the field."""
    row = await _create(auth_client, admin_token, slug="cert", ca_cert_pem=_PEM)
    assert row["ca_cert_pem"] == _PEM
    upd = await auth_client.put(
        f"/api/v1/proxmox-clusters/{row['id']}",
        json={"ca_cert_pem": ""},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["ca_cert_pem"] is None


async def test_update_ignores_explicit_null_on_not_nullable_fields(auth_client, admin_token):
    """`{"name": null}` (explicit null, not omitted) must not 500 on the NOT
    NULL column — it should be treated as "leave unchanged"."""
    row = await _create(auth_client, admin_token, slug="nullcheck")
    upd = await auth_client.put(
        f"/api/v1/proxmox-clusters/{row['id']}",
        json={"name": None, "endpoint": None, "api_token_id": None, "tls_insecure": None},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == row["name"]
    assert upd.json()["endpoint"] == row["endpoint"]
    assert upd.json()["api_token_id"] == row["api_token_id"]
    assert upd.json()["tls_insecure"] == row["tls_insecure"]


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
        {"endpoint": "https://pve.local:8006/#v1:0:18"},  # pasted Proxmox UI URL
        {"endpoint": "https://pve:8006/?x=1"},  # query string
        {"endpoint": "https://////"},  # empty host
        {"endpoint": "https://pve:8006/foo"},  # unrecognised path
    ],
)
async def test_validation_422(auth_client, admin_token, over):
    r = await auth_client.post("/api/v1/proxmox-clusters", json=_body(**over), headers=_h(admin_token))
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "slug,raw",
    [
        ("apipath", "https://pve.local:8006/api2/json/"),
        ("bare", "pve.local:8006"),  # no scheme → https assumed
        ("upper", "HTTPS://PVE.LOCAL:8006/"),  # scheme + host lower-cased
    ],
)
async def test_endpoint_normalises_to_bare_origin(auth_client, admin_token, slug, raw):
    row = await _create(auth_client, admin_token, slug=slug, endpoint=raw)
    assert row["endpoint"] == "https://pve.local:8006"


async def test_rbac_viewer_cannot_create_but_can_list(auth_client, viewer_token, admin_token):
    await _create(auth_client, admin_token, slug="rbac")
    r = await auth_client.post("/api/v1/proxmox-clusters", json=_body(slug="vwr"), headers=_h(viewer_token))
    assert r.status_code == 403
    r = await auth_client.get("/api/v1/proxmox-clusters", headers=_h(viewer_token))
    assert r.status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("POST", "", _body(slug="rbac-post")),
    ("PUT", "/{pk}", {}),
    ("DELETE", "/{pk}", None),
    ("POST", "/{pk}/test", None),
])
@pytest.mark.parametrize("role", ["viewer", "operator"])
async def test_rbac_non_admin_forbidden_on_writes(
    auth_client, admin_token, viewer_token, operator_token, method, path, body, role
):
    """viewer/operator are both below `admin` — every write endpoint (create,
    update, delete, test-connection) must 403 for them, not just viewer.
    (Both token fixtures are async, so both are requested up front rather
    than looked up dynamically via `request.getfixturevalue` — that deadlocks
    under pytest-asyncio's event loop.)"""
    token = {"viewer": viewer_token, "operator": operator_token}[role]
    row = await _create(auth_client, admin_token, slug="rbac-write")
    url = "/api/v1/proxmox-clusters" + path.format(pk=row["id"])
    kwargs = {"headers": _h(token)}
    if body is not None:
        kwargs["json"] = body
    r = await auth_client.request(method, url, **kwargs)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", ["viewer", "operator"])
async def test_rbac_non_admin_can_list(auth_client, admin_token, viewer_token, operator_token, role):
    token = {"viewer": viewer_token, "operator": operator_token}[role]
    await _create(auth_client, admin_token, slug="rbac-read")
    r = await auth_client.get("/api/v1/proxmox-clusters", headers=_h(token))
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
            id="pmx-other", business_unit_id="bu-other", slug="prx", name="prx",
            endpoint="https://prx:8006", api_token_id="u@pam!t",
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
    row = await _create(auth_client, admin_token, slug="okay", tls_insecure=True)
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
