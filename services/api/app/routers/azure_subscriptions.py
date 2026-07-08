"""Azure subscription CRUD with encrypted SP secrets at rest."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.azure_subscription import AzureSubscription
from app.models.user import User
from app.schemas.azure_subscription import (
    AzureContainerResult,
    AzureSubscriptionCreate,
    AzureSubscriptionResponse,
    AzureSubscriptionTestResult,
    AzureSubscriptionUpdate,
)
from app.services import azure_subscription_service as svc
from app.services.azure_blob_state_service import AzureBlobStateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/azure-subscriptions", tags=["azure-subscriptions"])


def _to_response(sub: AzureSubscription) -> AzureSubscriptionResponse:
    try:
        plain = svc.decrypt_secret(sub.client_secret_encrypted)
    except Exception:
        plain = ""
    return AzureSubscriptionResponse(
        id=sub.id,
        business_unit_id=sub.business_unit_id,
        subscription_id=sub.subscription_id,
        tenant_id=sub.tenant_id,
        client_id=sub.client_id,
        name=sub.name,
        description=sub.description,
        default_location=sub.default_location,
        state_storage_account=sub.state_storage_account,
        state_container=sub.state_container,
        client_secret_masked=svc.mask_secret_tail(plain) if plain else "(unreadable)",
    )


async def _scoped_subscription(db: AsyncSession, sub_pk: str, bu: BUScope) -> AzureSubscription:
    """Fetch a subscription by PK, enforcing the caller's BU scope (404 cross-BU)."""
    sub = await db.get(AzureSubscription, sub_pk)
    if sub is None or (bu.bu_id is not None and sub.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Azure subscription not found")
    return sub


@router.get("", response_model=list[AzureSubscriptionResponse])
async def list_azure_subscriptions(
    _: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    rows = await svc.list_subscriptions(db, business_unit_id=bu.bu_id)
    return [_to_response(s) for s in rows]


@router.post("", response_model=AzureSubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_azure_subscription(
    body: AzureSubscriptionCreate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a subscription",
        )
    # Uniqueness within a BU on the natural key (subscription_id).
    existing = (
        await db.execute(
            select(AzureSubscription).where(
                AzureSubscription.business_unit_id == bu.bu_id,
                AzureSubscription.subscription_id == body.subscription_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Azure subscription {body.subscription_id} is already configured in this business unit",
        )
    sub = AzureSubscription(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        subscription_id=body.subscription_id,
        tenant_id=body.tenant_id,
        client_id=body.client_id,
        name=body.name,
        description=body.description,
        default_location=body.default_location,
        state_storage_account=body.state_storage_account,
        state_container=body.state_container,
        client_secret_encrypted=svc.encrypt_secret(body.client_secret),
    )
    db.add(sub)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Azure subscription {body.subscription_id} is already configured",
        )
    await db.refresh(sub)
    return _to_response(sub)


@router.put("/{sub_pk}", response_model=AzureSubscriptionResponse)
async def update_azure_subscription(
    sub_pk: str,
    body: AzureSubscriptionUpdate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    sub = await _scoped_subscription(db, sub_pk, bu)
    data = body.model_dump(exclude_unset=True)
    new_secret = data.pop("client_secret", None)
    if new_secret is not None:
        sub.client_secret_encrypted = svc.encrypt_secret(new_secret)
    for k, v in data.items():
        setattr(sub, k, v)
    await db.commit()
    await db.refresh(sub)
    return _to_response(sub)


@router.delete("/{sub_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_azure_subscription(
    sub_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    sub = await _scoped_subscription(db, sub_pk, bu)
    await db.delete(sub)
    await db.commit()


@router.post("/{sub_pk}/test", response_model=AzureSubscriptionTestResult)
async def test_azure_subscription(
    sub_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Validate the SP creds by requesting an ARM access token.

    Uses azure-identity if installed; falls back to a raw HTTPS POST to the
    OAuth2 token endpoint so the API container doesn't grow a hard
    dependency on the azure-sdk just for credential validation.
    """
    sub = await _scoped_subscription(db, sub_pk, bu)
    try:
        secret = svc.decrypt_secret(sub.client_secret_encrypted)
        try:
            import httpx
        except Exception:
            return AzureSubscriptionTestResult(
                ok=False, detail="httpx not available in API image; cannot test"
            )
        resp = httpx.post(
            f"https://login.microsoftonline.com/{sub.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": sub.client_id,
                "client_secret": secret,
                "scope": "https://management.azure.com/.default",
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            return AzureSubscriptionTestResult(
                ok=False,
                detail=f"Token endpoint returned {resp.status_code}: {resp.text[:200]}",
            )
        # If a Blob state container is configured, verify the SP can actually
        # reach it (i.e. it holds Storage Blob Data Contributor) before an
        # operator flips a workspace to state_backend=azureblob.
        if sub.state_storage_account and sub.state_container:
            try:
                AzureBlobStateService.verify_container(
                    sub.state_storage_account,
                    sub.state_container,
                    sub.tenant_id,
                    sub.client_id,
                    secret,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Azure state container probe failed for subscription %s",
                    sub.subscription_id,
                    exc_info=True,
                )
                return AzureSubscriptionTestResult(
                    ok=False,
                    detail=f"SP token OK but state container unreachable: {str(e)[:150]}",
                )
            return AzureSubscriptionTestResult(
                ok=True, detail="SP credentials + Blob state container validated"
            )
        return AzureSubscriptionTestResult(ok=True, detail="SP credentials validated against ARM token endpoint")
    except Exception as e:  # noqa: BLE001
        logger.warning("Azure credential test failed for subscription %s", sub.subscription_id, exc_info=True)
        return AzureSubscriptionTestResult(ok=False, detail=str(e)[:200])


@router.post("/{sub_pk}/container", response_model=AzureContainerResult)
async def create_state_container(
    sub_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Create (or verify) the Blob container used for this subscription's TF state."""
    sub = await _scoped_subscription(db, sub_pk, bu)
    if not sub.state_storage_account or not sub.state_container:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set state_storage_account and state_container before creating the container",
        )
    try:
        secret = svc.decrypt_secret(sub.client_secret_encrypted)
        already = AzureBlobStateService.ensure_container(
            sub.state_storage_account,
            sub.state_container,
            sub.tenant_id,
            sub.client_id,
            secret,
        )
        return AzureContainerResult(
            ok=True,
            container=sub.state_container,
            already_existed=already,
            detail="Container already existed" if already else "Container created",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Azure container create failed for subscription %s", sub.subscription_id, exc_info=True)
        return AzureContainerResult(ok=False, container=sub.state_container, detail=str(e)[:200])
