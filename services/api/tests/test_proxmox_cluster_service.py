"""Service-layer coverage for proxmox_cluster_service: crypto context is
distinct from the other provider domains, endpoint normalisation, masking."""
import pytest

from app.services import gcp_project_service as gcpsvc
from app.services import proxmox_cluster_service as svc


def test_encrypt_decrypt_round_trip():
    assert svc.decrypt_secret(svc.encrypt_secret("s3cr3t")) == "s3cr3t"


def test_ciphertext_not_interchangeable_with_gcp_domain():
    # Same root key, different HKDF salt → the GCP context must reject it.
    token = svc.encrypt_secret("s3cr3t")
    with pytest.raises(RuntimeError):
        gcpsvc.decrypt_secret(token)


def test_decrypt_garbage_raises_runtime_error():
    with pytest.raises(RuntimeError):
        svc.decrypt_secret("not-a-fernet-token")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://pve.local:8006", "https://pve.local:8006"),
        ("https://pve.local:8006/", "https://pve.local:8006"),
        ("https://pve.local:8006/api2/json", "https://pve.local:8006"),
        ("https://pve.local:8006/api2/json/", "https://pve.local:8006"),
    ],
)
def test_normalize_endpoint(raw, expected):
    assert svc.normalize_endpoint(raw) == expected


def test_mask_tail():
    assert svc.mask_tail("abcdef12-3456") == "…3456"
    assert svc.mask_tail("ab") == "…"


async def test_get_cluster_credentials(db_session):
    from app.models.business_unit import DEFAULT_BU_ID, BusinessUnit
    from app.models.proxmox_cluster import ProxmoxCluster

    if await db_session.get(BusinessUnit, DEFAULT_BU_ID) is None:
        db_session.add(BusinessUnit(id=DEFAULT_BU_ID, slug="default", name="Default"))
    row = ProxmoxCluster(
        business_unit_id=DEFAULT_BU_ID, slug="home", name="Home",
        endpoint="https://pve.local:8006", api_token_id="tdt@pve!ci",
        api_token_secret_encrypted=svc.encrypt_secret("sek"),
        ssh_username="root", ssh_private_key_encrypted=svc.encrypt_secret("KEY"),
        tls_insecure=True, ca_cert_pem=None,
    )
    db_session.add(row)
    await db_session.commit()

    creds = await svc.get_cluster_credentials(db_session, row.id)
    assert creds is not None
    assert creds.endpoint == "https://pve.local:8006"
    assert creds.token_id == "tdt@pve!ci" and creds.token_secret == "sek"
    assert creds.ssh_username == "root" and creds.ssh_private_key == "KEY"
    assert creds.tls_insecure is True and creds.ca_cert_pem is None
    assert await svc.get_cluster_credentials(db_session, "nope") is None
