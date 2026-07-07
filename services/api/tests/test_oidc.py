"""Unit coverage for app.auth.oidc: auth-mode, config loading, discovery cache,
OAuth client build, role mapping, display-name extraction, and JIT user upsert."""
import pytest

from app.auth import oidc
from app.models.user import User

pytestmark = pytest.mark.usefixtures("default_bu")


def _cfg(**over):
    base = dict(
        issuer="https://idp.example.com/",
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://app/cb",
        scopes="openid email",
        role_claim="groups",
        role_mapping={"tdt-admins": "admin", "tdt-ops": "operator", "tdt-root": "superadmin"},
        default_role="viewer",
    )
    base.update(over)
    return oidc.OIDCConfig(**base)


# ─── auth mode ───────────────────────────────────────────────────────────────


async def test_get_auth_mode_default_and_override(db_session, monkeypatch):
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    assert await oidc.get_auth_mode(db_session) == "local"
    monkeypatch.setenv("AUTH_PROVIDER", "both")
    assert await oidc.get_auth_mode(db_session) == "both"
    monkeypatch.setenv("AUTH_PROVIDER", "bogus")
    assert await oidc.get_auth_mode(db_session) == "local"


# ─── config loading ──────────────────────────────────────────────────────────


async def test_load_oidc_config_missing_returns_none(db_session, monkeypatch):
    for k in ("AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET", "AUTH_OIDC_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    assert await oidc.load_oidc_config(db_session) is None


async def test_load_oidc_config_full_from_env(db_session, monkeypatch):
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_SECRET", "sec")
    monkeypatch.setenv("AUTH_OIDC_REDIRECT_URI", "https://app/cb")
    monkeypatch.setenv("AUTH_OIDC_ROLE_MAPPING", '{"g":"admin"}')
    cfg = await oidc.load_oidc_config(db_session)
    assert cfg is not None
    assert cfg.issuer == "https://idp.example.com/"  # normalized trailing slash
    assert cfg.scopes == "openid email profile groups"  # default
    assert cfg.role_mapping == {"g": "admin"}


async def test_load_oidc_config_bad_role_mapping_json(db_session, monkeypatch):
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "https://idp")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "c")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_SECRET", "s")
    monkeypatch.setenv("AUTH_OIDC_REDIRECT_URI", "https://cb")
    monkeypatch.setenv("AUTH_OIDC_ROLE_MAPPING", "not-json")
    cfg = await oidc.load_oidc_config(db_session)
    assert cfg.role_mapping == {}


async def test_env_or_cfg_fallback_to_config_table(db_session, monkeypatch):
    monkeypatch.delenv("AUTH_OIDC_ISSUER", raising=False)
    from app.services.config_service import ConfigService
    from app.auth.encryption_key import get_credential_encryption_key

    await ConfigService(db_session, get_credential_encryption_key()).set(
        "auth.oidc.issuer", "https://from-db/"
    )
    await db_session.commit()
    val = await oidc._env_or_cfg(db_session, "AUTH_OIDC_ISSUER", "auth.oidc.issuer")
    assert val == "https://from-db/"


# ─── discovery + client ──────────────────────────────────────────────────────


async def test_discover_fetches_then_caches(monkeypatch):
    oidc._DISCOVERY_CACHE.clear()
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"issuer": "x"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def get(self, url):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", _Client)
    doc1 = await oidc._discover("https://idp/")
    doc2 = await oidc._discover("https://idp/")  # cache hit, no 2nd fetch
    assert doc1 == {"issuer": "x"} and calls["n"] == 1 and doc2 == doc1


def test_build_oauth_client_and_state_token():
    oauth = oidc.build_oauth_client(_cfg())
    assert oauth is not None
    assert isinstance(oidc.new_state_token(), str) and len(oidc.new_state_token()) > 10


# ─── role mapping ────────────────────────────────────────────────────────────


def test_role_for_user_no_claim_defaults():
    assert oidc.role_for_user({}, _cfg()) == ("viewer", False)


def test_role_for_user_superadmin_and_priority():
    cfg = _cfg()
    # superadmin mapping → ("admin", True)
    assert oidc.role_for_user({"groups": ["tdt-root", "tdt-ops"]}, cfg) == ("admin", True)
    # highest non-super priority wins
    assert oidc.role_for_user({"groups": ["tdt-ops", "tdt-admins"]}, cfg) == ("admin", False)


def test_role_for_user_case_insensitive_and_scalar():
    cfg = _cfg(role_mapping={"DevOps": "operator"})
    assert oidc.role_for_user({"groups": "devops"}, cfg) == ("operator", False)
    # non-string group entries are skipped; unmatched → default
    assert oidc.role_for_user({"groups": [123, "nope"]}, cfg) == ("viewer", False)


# ─── display name ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"name": "Alex Rivera"}, "Alex Rivera"),
        ({"given_name": "Alex", "family_name": "Rivera"}, "Alex Rivera"),
        ({"given_name": "Alex"}, "Alex"),
        ({"preferred_username": "arivera"}, "arivera"),
        ({}, None),
        ({"name": "   "}, None),
    ],
)
def test_display_name_from_claims(claims, expected):
    assert oidc.display_name_from_claims(claims) == expected


# ─── JIT upsert ──────────────────────────────────────────────────────────────


async def test_upsert_provisions_new_user(db_session):
    u = await oidc.upsert_oidc_user(
        db_session, sub="sub-1", email="new@x.com", role="operator",
        is_superadmin=False, display_name="New User",
    )
    assert u.auth_provider == "oidc" and u.external_id == "sub-1" and u.role == "operator"


async def test_upsert_links_existing_by_email_then_external_id(db_session):
    # Pre-existing local user with same email, no external_id.
    db_session.add(
        User(id="u-local", email="dup@x.com", hashed_password="h", role="viewer", auth_provider="local")
    )
    await db_session.commit()
    u = await oidc.upsert_oidc_user(
        db_session, sub="sub-9", email="dup@x.com", role="admin", is_superadmin=True,
        email_verified=True,
    )
    assert u.id == "u-local" and u.external_id == "sub-9" and u.is_superadmin is True
    # Second login matches by external_id; None display_name doesn't wipe existing.
    u.display_name = "Keep Me"
    await db_session.commit()
    again = await oidc.upsert_oidc_user(
        db_session, sub="sub-9", email="dup@x.com", role="viewer", is_superadmin=False,
        display_name=None,
    )
    assert again.id == "u-local" and again.role == "viewer" and again.is_superadmin is False
    assert again.display_name == "Keep Me"
    # Third login with a fresh name → display_name is refreshed from the IdP.
    renamed = await oidc.upsert_oidc_user(
        db_session, sub="sub-9", email="dup@x.com", role="viewer", display_name="Renamed",
        email_verified=True,
    )
    assert renamed.display_name == "Renamed"


async def test_upsert_refuses_unverified_email_link(db_session):
    """linking to a pre-existing account by an UNVERIFIED email is
    an account-takeover vector and must be refused."""
    db_session.add(
        User(id="admin-local", email="admin@x.com", hashed_password="h",
             role="admin", is_superadmin=True, auth_provider="local")
    )
    await db_session.commit()
    # Attacker's IdP account: same email, unverified, different sub.
    with pytest.raises(oidc.UnverifiedEmailLinkError):
        await oidc.upsert_oidc_user(
            db_session, sub="attacker-sub", email="admin@x.com",
            role="admin", is_superadmin=True, email_verified=False,
        )
    # The existing admin row is untouched (not relinked).
    row = await db_session.get(User, "admin-local")
    assert row.external_id is None and row.auth_provider == "local"
