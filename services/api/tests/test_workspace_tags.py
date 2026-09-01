"""Key/value tags on workspaces: validation, filtering, bulk edit, discovery."""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID
from app.services import workspace_tags as wt


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _seed(factory, specs, bu_id=DEFAULT_BU_ID):
    """specs: [(name, tags_or_None), …] → {name: id}"""
    from app.models.workspace import Workspace

    out = {}
    async with factory() as session:
        for name, tags in specs:
            ws_id = str(uuid.uuid4())
            session.add(Workspace(
                business_unit_id=bu_id, id=ws_id, name=name,
                aws_account_id="123456789012", region="us-east-1",
                environment="dev", tf_working_dir=f"a/b/{name}",
                repo_url="https://example.com/x.git", tags=tags,
            ))
            out[name] = ws_id
        await session.commit()
    return out


# ─── validation (pure) ──────────────────────────────────────────────────────


def test_keys_are_lowercased():
    """`Team` and `team` being distinct tags is a bug every tagging system
    learns the hard way."""
    assert wt.validate({"Team": "Payments"}) == {"team": "Payments"}


def test_values_keep_their_case():
    assert wt.validate({"owner": "Jane Doe"})["owner"] == "Jane Doe"


def test_collision_after_normalization_is_rejected():
    with pytest.raises(wt.TagError, match="duplicate"):
        wt.validate({"Team": "a", "team": "b"})


@pytest.mark.parametrize("bad", ["", " ", "-lead", "trail-", "has space", "UPPER!", "a" * 65])
def test_invalid_keys_are_rejected(bad):
    with pytest.raises(wt.TagError):
        wt.validate({bad: "v"})


def test_non_scalar_values_are_rejected():
    with pytest.raises(wt.TagError, match="scalar"):
        wt.validate({"team": {"nested": 1}})


def test_scalars_are_coerced_to_strings():
    assert wt.validate({"port": 8080, "on": True})["port"] == "8080"


def test_tag_count_is_capped():
    with pytest.raises(wt.TagError, match="at most"):
        wt.validate({f"k{i}": "v" for i in range(wt.MAX_TAGS_PER_WORKSPACE + 1)})


def test_oversized_value_is_rejected():
    with pytest.raises(wt.TagError, match="exceeds"):
        wt.validate({"k": "x" * (wt.VALUE_MAX + 1)})


def test_filter_parsing():
    assert wt.parse_filter("team=payments") == ("team", "payments")
    assert wt.parse_filter("Team=Payments") == ("team", "Payments")
    assert wt.parse_filter("owner") == ("owner", None)
    with pytest.raises(wt.TagError):
        wt.parse_filter("")


def test_bare_key_matches_any_value():
    assert wt.matches({"owner": "jane"}, [("owner", None)])
    assert not wt.matches({"team": "x"}, [("owner", None)])


def test_filters_are_anded():
    tags = {"team": "pay", "tier": "prod"}
    assert wt.matches(tags, [("team", "pay"), ("tier", "prod")])
    assert not wt.matches(tags, [("team", "pay"), ("tier", "dev")])


def test_untagged_matches_nothing_but_survives():
    assert not wt.matches(None, [("team", None)])
    assert wt.matches(None, [])


def test_apply_edit_merges_per_key():
    """The property that makes bulk edit safe: setting `team` across twenty
    workspaces must not wipe the `owner` one of them carries."""
    assert wt.apply_edit({"owner": "jane"}, {"team": "pay"}) == {"owner": "jane", "team": "pay"}


def test_apply_edit_unsets_and_is_idempotent():
    assert wt.apply_edit({"a": "1", "b": "2"}, None, ["a"]) == {"b": "2"}
    assert wt.apply_edit({"b": "2"}, None, ["a"]) == {"b": "2"}


def test_apply_edit_unset_is_case_insensitive():
    assert wt.apply_edit({"team": "x"}, None, ["Team"]) == {}


# ─── API: create / update ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_read_back_tags(auth_client, admin_token, default_aws_account):
    r = await auth_client.post("/api/v1/workspaces", headers=_h(admin_token), json={
        "name": "tagged", "environment": "dev", "aws_account_id": "123456789012",
        "region": "us-east-1", "tf_working_dir": "a/b/tagged",
        "repo_url": "https://example.com/x.git", "tags": {"Team": "payments"},
    })
    assert r.status_code == 201, r.text
    assert r.json()["tags"] == {"team": "payments"}


@pytest.mark.asyncio
async def test_untagged_workspace_reports_an_empty_object_not_null(
    auth_client, admin_token, _setup_db
):
    """Every consumer wants to iterate tags without a null guard."""
    await _seed(_setup_db, [("plain", None)])
    r = await auth_client.get("/api/v1/workspaces", headers=_h(admin_token))
    assert next(w for w in r.json() if w["name"] == "plain")["tags"] == {}


@pytest.mark.asyncio
async def test_invalid_tags_on_create_are_a_400(auth_client, admin_token, default_aws_account):
    r = await auth_client.post("/api/v1/workspaces", headers=_h(admin_token), json={
        "name": "bad", "environment": "dev", "aws_account_id": "123456789012",
        "region": "us-east-1", "tf_working_dir": "a/b/bad",
        "repo_url": "https://example.com/x.git", "tags": {"has space": "v"},
    })
    assert r.status_code == 400
    assert "invalid tag key" in r.json()["detail"]


@pytest.mark.asyncio
async def test_put_replaces_the_whole_map_and_validates(auth_client, admin_token, _setup_db):
    ids = await _seed(_setup_db, [("w", {"a": "1", "b": "2"})])
    r = await auth_client.put(
        f"/api/v1/workspaces/{ids['w']}", headers=_h(admin_token), json={"tags": {"c": "3"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == {"c": "3"}

    bad = await auth_client.put(
        f"/api/v1/workspaces/{ids['w']}", headers=_h(admin_token), json={"tags": {"BAD KEY": "x"}}
    )
    assert bad.status_code == 400


# ─── API: filtering ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_by_key_and_value(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, [
        ("pay-prod", {"team": "payments", "tier": "prod"}),
        ("pay-dev", {"team": "payments", "tier": "dev"}),
        ("ops-prod", {"team": "ops", "tier": "prod"}),
        ("bare", None),
    ])
    r = await auth_client.get("/api/v1/workspaces?tag=team=payments", headers=_h(admin_token))
    assert {w["name"] for w in r.json()} == {"pay-prod", "pay-dev"}


@pytest.mark.asyncio
async def test_repeated_tag_params_are_anded(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, [
        ("pay-prod", {"team": "payments", "tier": "prod"}),
        ("pay-dev", {"team": "payments", "tier": "dev"}),
    ])
    r = await auth_client.get(
        "/api/v1/workspaces?tag=team=payments&tag=tier=prod", headers=_h(admin_token)
    )
    assert {w["name"] for w in r.json()} == {"pay-prod"}


@pytest.mark.asyncio
async def test_bare_key_filter_matches_any_value(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, [("has", {"owner": "jane"}), ("hasnt", {"team": "x"})])
    r = await auth_client.get("/api/v1/workspaces?tag=owner", headers=_h(admin_token))
    assert {w["name"] for w in r.json()} == {"has"}


@pytest.mark.asyncio
async def test_no_tag_param_returns_everything(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, [("a", {"t": "1"}), ("b", None)])
    r = await auth_client.get("/api/v1/workspaces", headers=_h(admin_token))
    assert {w["name"] for w in r.json()} >= {"a", "b"}


@pytest.mark.asyncio
async def test_malformed_filter_is_a_400(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, [("a", {"t": "1"})])
    r = await auth_client.get("/api/v1/workspaces?tag==novalue", headers=_h(admin_token))
    assert r.status_code == 400


# ─── API: bulk edit ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_set_merges_and_does_not_clobber(auth_client, admin_token, _setup_db):
    """The whole reason `set` is a merge and not a replace."""
    ids = await _seed(_setup_db, [("a", {"owner": "jane"}), ("b", {"tier": "prod"})])
    r = await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": list(ids.values()), "set": {"team": "payments"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2
    by_name = {w["name"]: w["tags"] for w in r.json()["workspaces"]}
    assert by_name["a"] == {"owner": "jane", "team": "payments"}
    assert by_name["b"] == {"tier": "prod", "team": "payments"}


@pytest.mark.asyncio
async def test_bulk_unset(auth_client, admin_token, _setup_db):
    ids = await _seed(_setup_db, [("a", {"team": "x", "keep": "y"})])
    r = await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": [ids["a"]], "unset": ["team"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["workspaces"][0]["tags"] == {"keep": "y"}


@pytest.mark.asyncio
async def test_bulk_edit_persists(auth_client, admin_token, _setup_db):
    """Guards the SQLAlchemy trap: mutating a JSON dict in place is not tracked."""
    ids = await _seed(_setup_db, [("a", {"old": "1"})])
    await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": [ids["a"]], "set": {"new": "2"},
    })
    r = await auth_client.get(f"/api/v1/workspaces/{ids['a']}", headers=_h(admin_token))
    assert r.json()["tags"] == {"old": "1", "new": "2"}


@pytest.mark.asyncio
async def test_bulk_edit_is_all_or_nothing(auth_client, admin_token, _setup_db):
    """A partial bulk edit is worse than a rejected one — you can't tell which
    half applied."""
    ids = await _seed(_setup_db, [("a", {"x": "1"})])
    ghost = str(uuid.uuid4())
    r = await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": [ids["a"], ghost], "set": {"team": "pay"},
    })
    assert r.status_code == 404
    assert ghost in r.json()["detail"]

    check = await auth_client.get(f"/api/v1/workspaces/{ids['a']}", headers=_h(admin_token))
    assert check.json()["tags"] == {"x": "1"}, "a rejected batch still wrote"


@pytest.mark.asyncio
async def test_bulk_edit_cannot_reach_another_bu(auth_client, admin_token, _setup_db):
    from app.models.business_unit import BusinessUnit

    other = str(uuid.uuid4())
    async with _setup_db() as session:
        session.add(BusinessUnit(id=other, slug="other-tags", name="Other"))
        await session.commit()
    foreign = await _seed(_setup_db, [("foreign", {"a": "1"})], bu_id=other)

    r = await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": [foreign["foreign"]], "set": {"team": "pay"},
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_edit_with_nothing_to_do_is_a_400(auth_client, admin_token, _setup_db):
    ids = await _seed(_setup_db, [("a", None)])
    r = await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": [ids["a"]],
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_edit_requires_operator(auth_client, viewer_token, _setup_db):
    ids = await _seed(_setup_db, [("a", None)])
    r = await auth_client.post("/api/v1/workspaces/tags", headers=_h(viewer_token), json={
        "workspace_ids": [ids["a"]], "set": {"team": "x"},
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bulk_edit_writes_one_audit_row_per_workspace(
    auth_client, admin_token, _setup_db
):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    ids = await _seed(_setup_db, [("a", None), ("b", None)])
    await auth_client.post("/api/v1/workspaces/tags", headers=_h(admin_token), json={
        "workspace_ids": list(ids.values()), "set": {"team": "pay"},
    })
    async with _setup_db() as session:
        rows = (await session.execute(
            select(AuditLog).where(AuditLog.action == "workspace.tags_bulk_edit")
        )).scalars().all()
    assert {r.resource_id for r in rows} == set(ids.values())


# ─── API: discovery ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tag_index_counts_keys_and_values(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, [
        ("a", {"team": "pay", "tier": "prod"}),
        ("b", {"team": "pay"}),
        ("c", {"team": "ops"}),
        ("d", None),
    ])
    r = await auth_client.get("/api/v1/workspaces/tags", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    idx = {k["key"]: k for k in r.json()}
    assert idx["team"]["count"] == 3
    # values sorted by descending usage
    assert [v["value"] for v in idx["team"]["values"]] == ["pay", "ops"]
    assert idx["team"]["values"][0]["count"] == 2
    assert idx["tier"]["count"] == 1


@pytest.mark.asyncio
async def test_tag_index_is_bu_scoped(auth_client, admin_token, _setup_db):
    from app.models.business_unit import BusinessUnit

    other = str(uuid.uuid4())
    async with _setup_db() as session:
        session.add(BusinessUnit(id=other, slug="other-idx", name="Other"))
        await session.commit()
    await _seed(_setup_db, [("mine", {"visible": "1"})])
    await _seed(_setup_db, [("theirs", {"secret": "1"})], bu_id=other)

    r = await auth_client.get("/api/v1/workspaces/tags", headers=_h(admin_token))
    keys = {k["key"] for k in r.json()}
    assert "visible" in keys and "secret" not in keys


@pytest.mark.asyncio
async def test_tags_route_is_not_shadowed_by_the_id_route(auth_client, admin_token, _setup_db):
    """`/workspaces/tags` must not be parsed as workspace id "tags"."""
    await _seed(_setup_db, [("a", {"t": "1"})])
    r = await auth_client.get("/api/v1/workspaces/tags", headers=_h(admin_token))
    assert r.status_code == 200
    assert isinstance(r.json(), list)
