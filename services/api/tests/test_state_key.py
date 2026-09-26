"""State object key derivation (routers/state.py:_state_key_for).

Proxmox slugs are unique only per BU, so two BUs can both own
`proxmox/cluster-home/...`. Their state shares the fallback bucket, so the
key must carry the BU or one tenant reads/overwrites the other's state.
"""
from types import SimpleNamespace

from app.routers.state import _state_key_for


def _ws(path: str, bu: str = "bu-a-id", name: str = "ws") -> SimpleNamespace:
    return SimpleNamespace(tf_working_dir=path, business_unit_id=bu, name=name)


def test_proxmox_key_is_prefixed_with_business_unit():
    assert (
        _state_key_for(_ws("proxmox/cluster-home/pve/vms", bu="bu-1"))
        == "bu-bu-1/proxmox/cluster-home/pve/vms/terraform.tfstate"
    )


def test_same_proxmox_path_in_two_bus_gets_distinct_keys():
    path = "proxmox/cluster-home/pve/vms"
    assert _state_key_for(_ws(path, bu="bu-1")) != _state_key_for(_ws(path, bu="bu-2"))


def test_proxmox_prefix_match_is_case_insensitive():
    assert _state_key_for(_ws("/PROXMOX/cluster-home/pve/vms/", bu="bu-1")).startswith(
        "bu-bu-1/"
    )


def test_non_proxmox_keys_are_unchanged():
    assert (
        _state_key_for(_ws("account-111111111111/eu-central-1/shared"))
        == "account-111111111111/eu-central-1/shared/terraform.tfstate"
    )
    assert (
        _state_key_for(_ws("gcp/my-project/app"))
        == "gcp/my-project/app/terraform.tfstate"
    )
    assert _state_key_for(_ws(".", name="legacy")) == "legacy/terraform.tfstate"
