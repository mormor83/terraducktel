"""Azure Blob Storage backend for Terraform state.

Implements the ``StateStore`` protocol. Authenticates with the *same* Azure
service principal already stored on the workspace's `azure_subscriptions` row
(reused via AAD) — no separate storage secret. The SP must hold the
**Storage Blob Data Contributor** role on the target storage account/container.

The blob name is the workspace's ``{tf_working_dir}/terraform.tfstate`` key
verbatim, so the layout in Blob mirrors the layout in git and in S3.

The Azure SDK is imported lazily inside each method/ctor so the S3-only boot
path never requires the `azure-*` wheels to be installed.
"""
from __future__ import annotations

from typing import Optional


def _blob_service(storage_account: str, tenant_id: str, client_id: str, client_secret: str):
    from azure.identity import ClientSecretCredential
    from azure.storage.blob import BlobServiceClient

    credential = ClientSecretCredential(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
    )
    return BlobServiceClient(
        account_url=f"https://{storage_account}.blob.core.windows.net",
        credential=credential,
    )


class AzureBlobStateService:
    """StateStore backed by an Azure Blob container (AAD / service-principal auth)."""

    def __init__(
        self,
        storage_account: str,
        container: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ):
        self._service = _blob_service(storage_account, tenant_id, client_id, client_secret)
        self._container = container

    def get_state_at(self, key: str) -> Optional[bytes]:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._service.get_blob_client(self._container, key)
        try:
            return blob.download_blob().readall()
        except ResourceNotFoundError:
            return None

    def put_state_at(self, key: str, state_bytes: bytes) -> None:
        # Azure Blob is encrypted at rest with Microsoft-managed keys by
        # default — no explicit SSE parameter (that is an S3-only concept).
        blob = self._service.get_blob_client(self._container, key)
        blob.upload_blob(state_bytes, overwrite=True)

    def delete_state_at(self, key: str) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._service.get_blob_client(self._container, key)
        try:
            blob.delete_blob()
            return True
        except ResourceNotFoundError:
            return True

    # ------------------------------------------------------------------
    # Admin helpers (used by the azure-subscriptions router /container + /test)
    # ------------------------------------------------------------------
    @staticmethod
    def ensure_container(
        storage_account: str,
        container: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> bool:
        """Create the container if absent. Returns True if it already existed."""
        from azure.core.exceptions import ResourceExistsError

        service = _blob_service(storage_account, tenant_id, client_id, client_secret)
        try:
            service.create_container(container)
            return False
        except ResourceExistsError:
            return True

    @staticmethod
    def verify_container(
        storage_account: str,
        container: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Raise if the SP cannot read the container (used by /test to confirm
        the SP has Storage Blob Data Contributor before a workspace is flipped
        to ``state_backend=azureblob``)."""
        service = _blob_service(storage_account, tenant_id, client_id, client_secret)
        service.get_container_client(container).get_container_properties()
