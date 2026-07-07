import boto3
from botocore.exceptions import ClientError
from typing import Optional


class S3StateService:
    def __init__(
        self,
        bucket: str,
        use_localstack: bool = False,
        region: str = "us-east-1",
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ):
        """If access_key_id/secret_access_key are provided, the client uses them
        explicitly (one set of creds per AWS account, never inherited from the
        ambient environment). Otherwise boto3 falls back to its default chain —
        useful for LocalStack and tests.
        """
        self.bucket = bucket
        kwargs: dict = {"region_name": region}
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
        if use_localstack:
            from botocore.config import Config
            kwargs["endpoint_url"] = "http://localstack:4566"
            kwargs["config"] = Config(s3={"addressing_style": "path"})
        self._client = boto3.client("s3", **kwargs)

    def get_state_at(self, key: str) -> Optional[bytes]:
        """Read state at an explicit S3 key (used when the workspace has its
        own per-account bucket and the key mirrors `tf_working_dir/terraform.tfstate`).
        """
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise

    def delete_state_at(self, key: str) -> bool:
        """Delete an object at an explicit S3 key.

        Returns True on a successful delete or NoSuchKey/NoSuchBucket
        (the desired end state already holds). Raises on anything else
        so the caller can decide whether to abort or proceed.
        """
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NoSuchBucket"):
                return True
            raise

    def put_state_at(self, key: str, state_bytes: bytes) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=state_bytes,
            ServerSideEncryption="AES256",
        )

    def _state_key(self, account_id: str, environment: str, workspace_name: str, region: str = "") -> str:
        # Per-leaf isolation across regions: account/region/env/name. Region is
        # optional for backward compatibility — callers that don't pass it get
        # the original layout.
        if region:
            return f"tfstate/{account_id}/{region}/{environment}/{workspace_name}/terraform.tfstate"
        return f"tfstate/{account_id}/{environment}/{workspace_name}/terraform.tfstate"

    def get_state(
        self, account_id: str, environment: str, workspace_name: str, region: str = ""
    ) -> Optional[bytes]:
        try:
            resp = self._client.get_object(
                Bucket=self.bucket,
                Key=self._state_key(account_id, environment, workspace_name, region),
            )
            return resp["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise

    def put_state(
        self,
        account_id: str,
        environment: str,
        workspace_name: str,
        state_bytes: bytes,
        region: str = "",
    ) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self._state_key(account_id, environment, workspace_name, region),
            Body=state_bytes,
            ServerSideEncryption="AES256",
        )
