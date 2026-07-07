"""AWS account CRUD with encrypted credentials at rest."""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.aws_account import AwsAccount
from app.models.user import User
from app.schemas.aws_account import (
    AwsAccountCreate,
    AwsAccountResponse,
    AwsAccountTestResult,
    AwsAccountUpdate,
    CreateBucketResult,
)
from app.services import aws_account_service as accs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/aws-accounts", tags=["aws-accounts"])


def _to_response(acc: AwsAccount) -> AwsAccountResponse:
    plain_key = ""
    try:
        plain_key = accs.decrypt_secret(acc.access_key_id_encrypted)
    except Exception:
        # Decryption failure usually means the encryption key changed — surface
        # the row but mark the key unreadable rather than 500-ing the list.
        plain_key = ""
    return AwsAccountResponse(
        id=acc.id,
        business_unit_id=acc.business_unit_id,
        account_id=acc.account_id,
        name=acc.name,
        description=acc.description,
        state_bucket=acc.state_bucket,
        state_bucket_region=acc.state_bucket_region,
        default_region=acc.default_region,
        aws_profile_name=acc.aws_profile_name,
        access_key_id_masked=accs.mask_access_key_tail(plain_key) if plain_key else "(unreadable)",
    )


async def _scoped_account(db: AsyncSession, account_pk: str, bu: BUScope) -> AwsAccount:
    """Fetch an AWS account by PK, enforcing the caller's BU scope.

    404 (not 403) on a cross-BU access so we don't leak the existence of another
    tenant's account. `bu.bu_id is None` means superadmin/all-BUs and bypasses
    the check, identical to the guard the list endpoint uses.
    """
    acc = await db.get(AwsAccount, account_pk)
    if acc is None or (bu.bu_id is not None and acc.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="AWS account not found")
    return acc


@router.get("", response_model=list[AwsAccountResponse])
async def list_aws_accounts(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    rows = await accs.list_accounts(db, business_unit_id=bu.bu_id)
    return [_to_response(a) for a in rows]


@router.post("", response_model=AwsAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_aws_account(
    body: AwsAccountCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    # New accounts always belong to the currently-scoped BU. Superadmin must
    # pick a concrete BU via X-Business-Unit header — refuse "all".
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating an account",
        )
    # Uniqueness is per-BU (see migration 018) — same account_id may legally
    # exist in two BUs, though it's discouraged. Check within the scoped BU.
    existing = (
        await db.execute(
            select(AwsAccount).where(
                AwsAccount.business_unit_id == bu.bu_id,
                AwsAccount.account_id == body.account_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AWS account {body.account_id} is already configured in this business unit",
        )
    acc = AwsAccount(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        account_id=body.account_id,
        name=body.name,
        description=body.description,
        state_bucket=body.state_bucket,
        state_bucket_region=body.state_bucket_region,
        default_region=body.default_region,
        aws_profile_name=body.aws_profile_name,
        access_key_id_encrypted=accs.encrypt_secret(body.access_key_id),
        secret_access_key_encrypted=accs.encrypt_secret(body.secret_access_key),
    )
    db.add(acc)
    try:
        await db.commit()
    except Exception:
        # Race: another caller created the same account_id between our SELECT
        # and INSERT. Surface the 409 the user expected.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AWS account {body.account_id} is already configured",
        )
    await db.refresh(acc)
    return _to_response(acc)


@router.put("/{account_pk}", response_model=AwsAccountResponse)
async def update_aws_account(
    account_pk: str,
    body: AwsAccountUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    acc = await _scoped_account(db, account_pk, bu)
    data = body.model_dump(exclude_unset=True)
    new_key = data.pop("access_key_id", None)
    new_secret = data.pop("secret_access_key", None)
    if new_key is not None:
        acc.access_key_id_encrypted = accs.encrypt_secret(new_key)
    if new_secret is not None:
        acc.secret_access_key_encrypted = accs.encrypt_secret(new_secret)
    for k, v in data.items():
        setattr(acc, k, v)
    await db.commit()
    await db.refresh(acc)
    return _to_response(acc)


@router.delete("/{account_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aws_account(
    account_pk: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    acc = await _scoped_account(db, account_pk, bu)
    await db.delete(acc)
    await db.commit()


@router.post("/{account_pk}/test", response_model=AwsAccountTestResult)
async def test_aws_account(
    account_pk: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Validate credentials against real AWS by calling sts:GetCallerIdentity
    and s3:HeadBucket.

    Per-account credentials are ALWAYS real AWS — `S3_USE_LOCALSTACK` only
    affects the legacy env-default fallback bucket, not configured accounts.
    """
    acc = await _scoped_account(db, account_pk, bu)
    try:
        import boto3
        access_key = accs.decrypt_secret(acc.access_key_id_encrypted)
        secret_key = accs.decrypt_secret(acc.secret_access_key_encrypted)
        kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": acc.state_bucket_region,
        }

        sts = boto3.client("sts", **kwargs)
        caller_arn = sts.get_caller_identity().get("Arn")

        s3 = boto3.client("s3", **kwargs)
        try:
            s3.head_bucket(Bucket=acc.state_bucket)
            bucket_exists = True
        except Exception:
            bucket_exists = False
        return AwsAccountTestResult(
            ok=True,
            caller_arn=caller_arn,
            bucket_exists=bucket_exists,
        )
    except Exception as e:  # noqa: BLE001 — bubble up sanitized error to UI
        logger.warning("AWS credential test failed for account %s", acc.account_id)
        return AwsAccountTestResult(ok=False, detail=str(e)[:200])


@router.post("/{account_pk}/bucket", response_model=CreateBucketResult)
async def create_state_bucket(
    account_pk: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Create the configured state bucket if it doesn't exist.

    Useful for both LocalStack (which has no buckets at start) and a real AWS
    account where you've onboarded creds before bootstrapping the bucket.
    Idempotent — returns `already_existed=True` when the bucket already exists.
    """
    acc = await _scoped_account(db, account_pk, bu)
    try:
        import boto3
        from botocore.exceptions import ClientError

        access_key = accs.decrypt_secret(acc.access_key_id_encrypted)
        secret_key = accs.decrypt_secret(acc.secret_access_key_encrypted)
        kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": acc.state_bucket_region,
        }
        s3 = boto3.client("s3", **kwargs)

        # Idempotency probe.
        try:
            s3.head_bucket(Bucket=acc.state_bucket)
            return CreateBucketResult(
                ok=True, detail="bucket already exists",
                bucket=acc.state_bucket, already_existed=True,
            )
        except ClientError:
            pass

        # us-east-1 must NOT have a LocationConstraint; every other region does.
        create_kwargs: dict = {"Bucket": acc.state_bucket}
        if acc.state_bucket_region and acc.state_bucket_region != "us-east-1":
            create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": acc.state_bucket_region}
        s3.create_bucket(**create_kwargs)

        # Hardening defaults — versioning, AES256, block public access.
        try:
            s3.put_bucket_versioning(
                Bucket=acc.state_bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
            s3.put_bucket_encryption(
                Bucket=acc.state_bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                },
            )
            s3.put_public_access_block(
                Bucket=acc.state_bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        except Exception:
            logger.warning(
                "Bucket %s created but post-create hardening failed",
                acc.state_bucket, exc_info=True,
            )

        return CreateBucketResult(
            ok=True,
            detail="created with versioning + AES256 + public-access-block",
            bucket=acc.state_bucket,
            already_existed=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Bucket creation failed for account %s", acc.account_id, exc_info=True)
        return CreateBucketResult(ok=False, detail=str(e)[:200], bucket=acc.state_bucket)
