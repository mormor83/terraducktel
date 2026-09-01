"""Profile resolution and the on-disk credential store."""
import json
import os
import stat

import pytest

from tdt import config, credentials as creds
from tdt.errors import ExitCode, TdtError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TDT_CONFIG_DIR", str(tmp_path))
    for var in ("TDT_API_URL", "TDT_BU", "TDT_TOKEN", "TDT_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ─── config ────────────────────────────────────────────────────────────────


def test_bare_host_gets_the_api_prefix_appended():
    config.save_profile("p", "https://tdt.example.com", "home", True)
    assert config.resolve("p").url == "https://tdt.example.com/api/v1"


def test_an_explicit_api_prefix_is_not_doubled():
    config.save_profile("p", "https://tdt.example.com/api/v1/", "home", True)
    assert config.resolve("p").url == "https://tdt.example.com/api/v1"


def test_a_single_profile_is_the_default_without_naming_it():
    config.save_profile("only", "https://x.example.com", "home", False)
    assert config.resolve().name == "only"


def test_flags_beat_env_which_beats_config(monkeypatch):
    config.save_profile("p", "https://config.example.com", "cfg-bu", True)

    assert config.resolve("p").bu == "cfg-bu"

    monkeypatch.setenv("TDT_BU", "env-bu")
    assert config.resolve("p").bu == "env-bu"

    assert config.resolve("p", bu="flag-bu").bu == "flag-bu"


def test_missing_url_is_a_usage_error_with_a_hint():
    with pytest.raises(TdtError) as exc:
        config.resolve()
    assert exc.value.code == ExitCode.USAGE
    assert "tdt profile add" in (exc.value.hint or "")


def test_unknown_profile_name_lists_the_known_ones():
    config.save_profile("prod", "https://x.example.com", "home", True)
    with pytest.raises(TdtError) as exc:
        config.resolve("staging")
    assert exc.value.code == ExitCode.USAGE
    assert "prod" in exc.value.message


def test_saving_a_profile_preserves_the_others():
    config.save_profile("a", "https://a.example.com", "bu-a", True)
    config.save_profile("b", "https://b.example.com", "bu-b", False)
    assert set(config.list_profiles()) == {"a", "b"}
    assert config.default_profile_name() == "a"


def test_making_a_profile_default_moves_the_default():
    config.save_profile("a", "https://a.example.com", None, True)
    config.save_profile("b", "https://b.example.com", None, True)
    assert config.default_profile_name() == "b"


def test_a_corrupt_config_is_a_usage_error_not_a_crash(isolated):
    (isolated / "config.toml").write_text("this is not = = toml [[[")
    with pytest.raises(TdtError) as exc:
        config.resolve("p")
    assert exc.value.code == ExitCode.USAGE


# ─── credentials ───────────────────────────────────────────────────────────


def test_credentials_file_is_owner_only(isolated):
    creds.store("p", creds.Credential(kind="api_key", api_key="tdt_secret"))
    mode = stat.S_IMODE(os.stat(isolated / "credentials.json").st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_store_and_load_roundtrip():
    creds.store("p", creds.Credential(kind="jwt", access_token="a", refresh_token="r"))
    got = creds.load("p")
    assert (got.kind, got.access_token, got.refresh_token) == ("jwt", "a", "r")


def test_profiles_do_not_share_credentials():
    creds.store("a", creds.Credential(kind="api_key", api_key="tdt_a"))
    creds.store("b", creds.Credential(kind="api_key", api_key="tdt_b"))
    assert creds.load("a").api_key == "tdt_a"
    assert creds.load("b").api_key == "tdt_b"


def test_clear_removes_only_the_named_profile():
    creds.store("a", creds.Credential(kind="api_key", api_key="tdt_a"))
    creds.store("b", creds.Credential(kind="api_key", api_key="tdt_b"))
    assert creds.clear("a") is True
    assert creds.load("a") is None
    assert creds.load("b") is not None
    assert creds.clear("a") is False  # already gone


def test_env_token_overrides_the_store(monkeypatch):
    """`tdt-flow.sh` parity — TDT_TOKEN wins, and `Bearer ` is tolerated."""
    creds.store("p", creds.Credential(kind="api_key", api_key="tdt_stored"))
    monkeypatch.setenv("TDT_TOKEN", "Bearer tdt_from_env")
    got = creds.load("p")
    assert got.kind == "api_key"
    assert got.api_key == "tdt_from_env"


def test_env_token_that_is_a_jwt_is_classified_as_a_jwt(monkeypatch):
    monkeypatch.setenv("TDT_TOKEN", "eyJhbGciOi.payload.sig")
    got = creds.load("p")
    assert got.kind == "jwt"
    assert got.access_token == "eyJhbGciOi.payload.sig"


def test_a_corrupt_credential_store_reads_as_absent(isolated):
    (isolated / "credentials.json").write_text("{not json")
    assert creds.load("p") is None


def test_jwt_claims_of_an_opaque_token_is_empty_not_an_exception():
    assert creds.jwt_claims("tdt_not_a_jwt") == {}
    assert creds.seconds_until_expiry("tdt_not_a_jwt") is None
