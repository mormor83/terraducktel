"""Google Cloud Storage backend for Terraform state.

Implements the ``StateStore`` protocol. Authenticates with the *same*
service-account JSON key already stored on the workspace's `gcp_projects` row
(reused) — no separate storage secret. The SA must hold
``roles/storage.objectAdmin`` (or finer) on the target bucket.

The object name is the workspace's ``{tf_working_dir}/terraform.tfstate`` key
verbatim, optionally under a configured prefix, so the layout mirrors git/S3.

The Google SDK is imported lazily so the S3-only boot path never requires the
`google-cloud-storage` wheel to be installed.
"""
from __future__ import annotations

import json
from typing import Optional


def _client(service_account_json: str, project_id: str):
    from google.cloud import storage
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(service_account_json)
    )
    return storage.Client(project=project_id, credentials=credentials)


class GcsStateService:
    """StateStore backed by a GCS bucket (service-account JSON auth)."""

    def __init__(
        self,
        bucket: str,
        service_account_json: str,
        project_id: str,
        prefix: str = "",
    ):
        self._bucket = _client(service_account_json, project_id).bucket(bucket)
        self._prefix = (prefix or "").strip("/")

    def _name(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def get_state_at(self, key: str) -> Optional[bytes]:
        from google.cloud.exceptions import NotFound

        blob = self._bucket.blob(self._name(key))
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None

    def put_state_at(self, key: str, state_bytes: bytes) -> None:
        # GCS encrypts at rest with Google-managed keys by default.
        blob = self._bucket.blob(self._name(key))
        blob.upload_from_string(state_bytes, content_type="application/json")

    def delete_state_at(self, key: str) -> bool:
        from google.cloud.exceptions import NotFound

        blob = self._bucket.blob(self._name(key))
        try:
            blob.delete()
            return True
        except NotFound:
            return True

    # ------------------------------------------------------------------
    # Admin helpers (used by the gcp-projects router /bucket + /test)
    # ------------------------------------------------------------------
    @staticmethod
    def ensure_bucket(
        bucket: str,
        service_account_json: str,
        project_id: str,
        region: str = "us-central1",
    ) -> bool:
        """Create the bucket (uniform bucket-level access + versioning) if absent.
        Returns True if it already existed."""
        from google.cloud.exceptions import Conflict

        client = _client(service_account_json, project_id)
        bucket_obj = client.bucket(bucket)
        if bucket_obj.exists():
            return True
        bucket_obj.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket_obj.versioning_enabled = True
        try:
            client.create_bucket(bucket_obj, location=region)
            return False
        except Conflict:
            return True

    @staticmethod
    def verify_bucket(bucket: str, service_account_json: str, project_id: str) -> None:
        """Raise if the SA cannot access the bucket (used by /test)."""
        _client(service_account_json, project_id).get_bucket(bucket)
