"""GCP project CRUD with encrypted service-account keys at rest."""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.gcp_project import GcpProject
from app.models.user import User
from app.schemas.gcp_project import (
    GcpBucketResult,
    GcpProjectCreate,
    GcpProjectResponse,
    GcpProjectTestResult,
    GcpProjectUpdate,
)
from app.services import gcp_project_service as svc
from app.services.gcs_state_service import GcsStateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gcp-projects", tags=["gcp-projects"])


def _to_response(proj: GcpProject) -> GcpProjectResponse:
    return GcpProjectResponse(
        id=proj.id,
        business_unit_id=proj.business_unit_id,
        project_id=proj.project_id,
        client_email=proj.client_email,
        name=proj.name,
        description=proj.description,
        default_region=proj.default_region,
        state_bucket=proj.state_bucket,
        state_prefix=proj.state_prefix,
        service_account_masked=svc.mask_sa(proj.client_email),
    )


async def _scoped_project(db: AsyncSession, project_pk: str, bu: BUScope) -> GcpProject:
    """Fetch a project by PK, enforcing the caller's BU scope (404 cross-BU)."""
    proj = await db.get(GcpProject, project_pk)
    if proj is None or (bu.bu_id is not None and proj.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="GCP project not found")
    return proj


@router.get("", response_model=list[GcpProjectResponse])
async def list_gcp_projects(
    _: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    rows = await svc.list_projects(db, business_unit_id=bu.bu_id)
    return [_to_response(p) for p in rows]


@router.post("", response_model=GcpProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_gcp_project(
    body: GcpProjectCreate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a project",
        )
    # The uploaded key must match the declared project_id.
    json_project_id, client_email = svc.parse_sa_json(body.service_account_json)
    if json_project_id and json_project_id != body.project_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"service-account key is for project {json_project_id!r}, "
                f"but project_id is {body.project_id!r}"
            ),
        )
    # Uniqueness within a BU on the natural key (project_id).
    existing = (
        await db.execute(
            select(GcpProject).where(
                GcpProject.business_unit_id == bu.bu_id,
                GcpProject.project_id == body.project_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"GCP project {body.project_id} is already configured in this business unit",
        )
    proj = GcpProject(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        project_id=body.project_id,
        client_email=client_email,
        name=body.name,
        description=body.description,
        default_region=body.default_region,
        state_bucket=body.state_bucket,
        state_prefix=body.state_prefix,
        service_account_json_encrypted=svc.encrypt_secret(body.service_account_json),
    )
    db.add(proj)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"GCP project {body.project_id} is already configured",
        )
    await db.refresh(proj)
    return _to_response(proj)


@router.put("/{project_pk}", response_model=GcpProjectResponse)
async def update_gcp_project(
    project_pk: str,
    body: GcpProjectUpdate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    proj = await _scoped_project(db, project_pk, bu)
    data = body.model_dump(exclude_unset=True)
    new_key = data.pop("service_account_json", None)
    if new_key is not None:
        json_project_id, client_email = svc.parse_sa_json(new_key)
        if json_project_id and json_project_id != proj.project_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"service-account key is for project {json_project_id!r}, "
                    f"but this row is project {proj.project_id!r}"
                ),
            )
        proj.service_account_json_encrypted = svc.encrypt_secret(new_key)
        proj.client_email = client_email
    for k, v in data.items():
        setattr(proj, k, v)
    await db.commit()
    await db.refresh(proj)
    return _to_response(proj)


@router.delete("/{project_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gcp_project(
    project_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    proj = await _scoped_project(db, project_pk, bu)
    await db.delete(proj)
    await db.commit()


@router.post("/{project_pk}/test", response_model=GcpProjectTestResult)
async def test_gcp_project(
    project_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Validate the SA key by minting an access token.

    Uses google-auth if installed; falls back to a clear "not available"
    message so credential validation never hard-crashes the endpoint.
    """
    proj = await _scoped_project(db, project_pk, bu)
    try:
        raw = svc.decrypt_secret(proj.service_account_json_encrypted)
        try:
            import google.auth.transport.requests
            from google.oauth2 import service_account
        except Exception:
            return GcpProjectTestResult(
                ok=False, detail="google-auth not available in API image; cannot test"
            )
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        return GcpProjectTestResult(
            ok=True,
            detail="Service-account key validated (token minted)",
            client_email=creds.service_account_email,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("GCP credential test failed for project %s", proj.project_id, exc_info=True)
        return GcpProjectTestResult(ok=False, detail=str(e)[:200])


@router.post("/{project_pk}/bucket", response_model=GcpBucketResult)
async def create_state_bucket(
    project_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Create (or verify) the GCS bucket used for this project's TF state."""
    proj = await _scoped_project(db, project_pk, bu)
    if not proj.state_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set state_bucket on this project before creating it",
        )
    try:
        raw = svc.decrypt_secret(proj.service_account_json_encrypted)
        already = GcsStateService.ensure_bucket(
            proj.state_bucket, raw, proj.project_id, proj.default_region
        )
        return GcpBucketResult(
            ok=True,
            bucket=proj.state_bucket,
            already_existed=already,
            detail="Bucket already existed" if already else "Bucket created",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("GCS bucket create failed for project %s", proj.project_id, exc_info=True)
        return GcpBucketResult(ok=False, bucket=proj.state_bucket, detail=str(e)[:200])
