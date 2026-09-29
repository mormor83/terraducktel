"""API tests for environment links: CRUD, pairing, BU-admin gate, BU scoping, audit."""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.models.audit_log import AuditLog
from app.models.business_unit import DEFAULT_BU_ID, BusinessUnit
from app.models.env_link import EnvPair
from app.models.user import User
from app.models.workspace import Workspace

pytestmark = pytest.mark.usefixtures("default_bu")

DEV = "account-333333333333"
PROD = "account-444444444444"
OTHER_BU_ID = "00000000-0000-0000-0000-0000000000b2"


async def _login(client, email):
    r = await client.post("/api/v1/auth/token", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def superadmin_headers(auth_client, seeded_users, _setup_db):
    async with _setup_db() as s:
        await s.execute(update(User).where(User.email == "admin@test.com").values(is_superadmin=True))
        await s.commit()
    h = await _login(auth_client, "admin@test.com")
    h["X-Business-Unit"] = "default"
    return h


def _ws(path, bu=DEFAULT_BU_ID, kind="terraform", acct=None):
    acct = acct or path.split("/")[0].removeprefix("account-")
    return Workspace(
        id=str(uuid.uuid4()),
        business_unit_id=bu,
        name=path.rsplit("/", 1)[-1],
        aws_account_id=acct[:12],
        environment="dev" if "6009" in acct else "prod",
        region="us-east-1",
        repo_url="https://github.com/acme/infra",
        tf_working_dir=path,
        repo_ref="main",
        kind=kind,
    )


@pytest_asyncio.fixture
async def stacks(_setup_db, default_bu):
    rows = [
        _ws(f"{DEV}/us-east-1/monitoring/stack"),
        _ws(f"{DEV}/us-east-1/vpc/home"),
        _ws(f"{DEV}/us-east-1/charts/grafana", kind="helm"),
        _ws(f"{PROD}/us-east-1/monitoring/stack"),
        _ws(f"{PROD}/us-east-1/legacy/thing"),
    ]
    async with _setup_db() as s:
        s.add_all(rows)
        await s.commit()
    return {r.tf_working_dir: r.id for r in rows}


def _body(name="dev-to-prod", **kw):
    b = {
        "name": name,
        "source_node": {"level": "account", "path": DEV},
        "target_node": {"level": "account", "path": PROD},
    }
    b.update(kw)
    return b


class TestPermissions:
    async def test_viewer_can_list_but_not_create(self, auth_client, viewer_token, stacks):
        h = {"Authorization": f"Bearer {viewer_token}"}
        assert (await auth_client.get("/api/v1/env-links", headers=h)).status_code == 200
        r = await auth_client.post("/api/v1/env-links", json=_body(), headers=h)
        assert r.status_code == 403

    async def test_global_admin_without_superadmin_is_not_bu_admin(self, auth_client, admin_token, stacks):
        h = {"Authorization": f"Bearer {admin_token}"}
        r = await auth_client.post("/api/v1/env-links", json=_body(), headers=h)
        assert r.status_code == 403
        assert r.json()["detail"] == "Requires Business Unit admin"

    async def test_operator_cannot_create(self, auth_client, operator_token, stacks):
        h = {"Authorization": f"Bearer {operator_token}"}
        assert (await auth_client.post("/api/v1/env-links", json=_body(), headers=h)).status_code == 403

    async def test_superadmin_owned_admin_api_key_is_not_bu_admin(self, auth_client, superadmin_headers, stacks):
        r = await auth_client.post(
            "/api/v1/api-keys", json={"name": "ci", "capability": "admin"}, headers=superadmin_headers
        )
        assert r.status_code == 201, r.text
        kh = {"Authorization": f"Bearer {r.json()['token']}"}
        assert (await auth_client.get("/api/v1/env-links", headers=kh)).status_code == 200
        assert (await auth_client.post("/api/v1/env-links", json=_body(), headers=kh)).status_code == 403

    async def test_superadmin_needs_a_specific_bu(self, auth_client, superadmin_headers, stacks):
        h = {**superadmin_headers, "X-Business-Unit": "all"}
        r = await auth_client.post("/api/v1/env-links", json=_body(), headers=h)
        assert r.status_code == 400
        assert "specific Business Unit" in r.json()["detail"]


class TestCrud:
    async def test_create_pairs_and_seeds_protected_rules(self, auth_client, superadmin_headers, stacks):
        r = await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)
        assert r.status_code == 201, r.text
        link = r.json()
        assert link["level"] == "account"
        assert link["source_account_id"] == "333333333333"
        assert link["target_account_id"] == "444444444444"
        assert link["pair_summary"] == {
            "not_compared": 1, "in_sync": 0, "diverged": 0, "missing_in_target": 1,
            "missing_in_source": 1, "excluded": 0, "total": 3,
        }
        assert link["helm_skipped"] == 1
        assert "333333333333" in link["protected_rules"]["values"]
        assert "instance_type" in link["protected_rules"]["keys"]
        assert link["rules_version"] == 1

    async def test_duplicate_name_conflicts(self, auth_client, superadmin_headers, stacks):
        assert (await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)).status_code == 201
        r = await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)
        assert r.status_code == 409

    async def test_level_mismatch_rejected(self, auth_client, superadmin_headers, stacks):
        body = _body(target_node={"level": "region", "path": f"{PROD}/us-east-1"})
        r = await auth_client.post("/api/v1/env-links", json=body, headers=superadmin_headers)
        assert r.status_code == 400
        assert "not a region" in r.json()["detail"] or "same level" in r.json()["detail"]

    async def test_bad_regex_rejected(self, auth_client, superadmin_headers, stacks):
        body = _body(rewrite_rules=[{"from": "(", "to": "x", "regex": True}])
        r = await auth_client.post("/api/v1/env-links", json=body, headers=superadmin_headers)
        assert r.status_code == 400

    async def test_update_bumps_rules_version_only_for_rule_changes(self, auth_client, superadmin_headers, stacks):
        link = (await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)).json()
        r = await auth_client.put(f"/api/v1/env-links/{link['id']}", json={"name": "renamed"}, headers=superadmin_headers)
        assert r.status_code == 200 and r.json()["rules_version"] == 1 and r.json()["name"] == "renamed"
        r = await auth_client.put(
            f"/api/v1/env-links/{link['id']}",
            json={"pair_overrides": {"exclude": [{"side": "target", "path": "us-east-1/legacy/thing"}]}},
            headers=superadmin_headers,
        )
        assert r.status_code == 200
        assert r.json()["rules_version"] == 2
        assert r.json()["pair_summary"]["excluded"] == 1
        assert r.json()["pair_summary"]["missing_in_source"] == 0

    async def test_pairs_endpoint_filters_and_names(self, auth_client, superadmin_headers, stacks):
        link = (await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)).json()
        r = await auth_client.get(f"/api/v1/env-links/{link['id']}/pairs?status=missing_in_target", headers=superadmin_headers)
        assert r.status_code == 200
        items = r.json()["items"]
        assert [i["source_rel"] for i in items] == ["us-east-1/vpc/home"]
        assert items[0]["source_stack_name"] == "home"
        assert items[0]["proposed_target_rel"] == "us-east-1/vpc/home"
        bad = await auth_client.get(f"/api/v1/env-links/{link['id']}/pairs?status=bogus", headers=superadmin_headers)
        assert bad.status_code == 400

    async def test_pair_ids_stable_and_reconciled_on_read(self, auth_client, superadmin_headers, stacks, _setup_db):
        link = (await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)).json()
        first = {p["relative_path"]: p["id"] for p in (await auth_client.get(
            f"/api/v1/env-links/{link['id']}/pairs", headers=superadmin_headers)).json()["items"]}
        # A new target stack appears (e.g. imported) → the missing pair becomes matched.
        async with _setup_db() as s:
            s.add(_ws(f"{PROD}/us-east-1/vpc/home"))
            await s.commit()
        items = (await auth_client.get(f"/api/v1/env-links/{link['id']}/pairs", headers=superadmin_headers)).json()["items"]
        now = {p["relative_path"]: p for p in items}
        assert now["us-east-1/monitoring/stack"]["id"] == first["us-east-1/monitoring/stack"]
        assert now["us-east-1/vpc/home"]["status"] == "not_compared"
        assert len([p for p in items if p["status"] == "missing_in_target"]) == 0

    async def test_delete_removes_pairs(self, auth_client, superadmin_headers, stacks, _setup_db):
        link = (await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)).json()
        r = await auth_client.delete(f"/api/v1/env-links/{link['id']}", headers=superadmin_headers)
        assert r.status_code == 200
        assert (await auth_client.get(f"/api/v1/env-links/{link['id']}", headers=superadmin_headers)).status_code == 404
        async with _setup_db() as s:
            assert (await s.execute(select(EnvPair).where(EnvPair.link_id == link["id"]))).first() is None


class TestPreview:
    async def test_preview_does_not_persist(self, auth_client, viewer_token, stacks):
        h = {"Authorization": f"Bearer {viewer_token}"}
        body = _body()
        del body["name"]
        r = await auth_client.post("/api/v1/env-links/preview-pairs", json=body, headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["summary"]["total"] == 3
        assert all(p["id"] is None for p in data["pairs"])
        assert "444444444444" in data["default_protected_rules"]["values"]
        assert (await auth_client.get("/api/v1/env-links", headers=h)).json() == []


class TestBuScoping:
    async def test_other_bu_link_is_invisible(self, auth_client, superadmin_headers, stacks, _setup_db, viewer_token):
        async with _setup_db() as s:
            s.add(BusinessUnit(id=OTHER_BU_ID, slug="other", name="Other"))
            s.add_all([
                _ws(f"{DEV}/us-east-1/x/stack", bu=OTHER_BU_ID),
                _ws(f"{PROD}/us-east-1/x/stack", bu=OTHER_BU_ID),
            ])
            await s.commit()
        h_other = {**superadmin_headers, "X-Business-Unit": "other"}
        link = (await auth_client.post("/api/v1/env-links", json=_body(), headers=h_other)).json()
        # Other BU's link pairs only its own stacks.
        assert link["pair_summary"]["total"] == 1
        vh = {"Authorization": f"Bearer {viewer_token}"}
        assert (await auth_client.get("/api/v1/env-links", headers=vh)).json() == []
        assert (await auth_client.get(f"/api/v1/env-links/{link['id']}", headers=vh)).status_code == 404


class TestAudit:
    async def test_writes_are_audited_and_visible_in_bu_audit(self, auth_client, superadmin_headers, stacks, _setup_db):
        link = (await auth_client.post("/api/v1/env-links", json=_body(), headers=superadmin_headers)).json()
        await auth_client.put(
            f"/api/v1/env-links/{link['id']}",
            json={"rewrite_rules": [{"from": "us-east-1", "to": "us-east-1"}]},
            headers=superadmin_headers,
        )
        async with _setup_db() as s:
            rows = (await s.execute(
                select(AuditLog).where(AuditLog.resource_type == "env_link").order_by(AuditLog.created_at)
            )).scalars().all()
        assert [r.action for r in rows] == ["env_link.create", "env_link.update"]
        assert rows[1].details["changes"]["rewrite_rules"]["new"] == [
            {"from": "us-east-1", "to": "us-east-1", "regex": False}
        ]
        assert rows[0].details["business_unit_id"] == DEFAULT_BU_ID
        # BU-scoped audit view includes the link rows.
        r = await auth_client.get("/api/v1/audit", headers=superadmin_headers)
        assert {"env_link.create", "env_link.update"} <= {i["action"] for i in r.json()["items"]}
