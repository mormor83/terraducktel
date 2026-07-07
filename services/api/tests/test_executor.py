import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestS3StateService:
    async def test_put_and_get_state(self, db_session):
        """State roundtrip via mocked S3"""
        with patch("app.services.s3_state_service.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.get_object.return_value = {
                "Body": MagicMock(read=lambda: b'{"version": 4}')
            }
            from app.services.s3_state_service import S3StateService
            svc = S3StateService(bucket="test-bucket", use_localstack=True)
            svc.put_state("123456789012", "dev", "vpc", b'{"version": 4}')
            state = svc.get_state("123456789012", "dev", "vpc")
            assert state == b'{"version": 4}'
            mock_client.put_object.assert_called_once()
            mock_client.get_object.assert_called_once()

    async def test_state_path_format(self):
        from app.services.s3_state_service import S3StateService
        svc = S3StateService(bucket="test-bucket", use_localstack=False)
        path = svc._state_key("123456789012", "prod", "my-workspace")
        assert path == "tfstate/123456789012/prod/my-workspace/terraform.tfstate"
