from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.auth.rbac import Principal
from app.database.session import get_session
from app.integrations.grafana_admin import ensure_platform, ensure_tenant_org, ensure_user
from app.models.entities import Tenant, User

router = APIRouter()


@router.get("/auth/grafana")
async def grafana_auth(principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    org_id = 1
    if principal.role not in {"super_admin", "noc_operator"}:
        if not principal.tenant_ids:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        tenant = await session.get(Tenant, next(iter(principal.tenant_ids)))
        if tenant is None:
            raise HTTPException(status_code=403, detail="Tenant access denied")
        org_id = await ensure_tenant_org(str(tenant.id), f"hmc-{tenant.slug}")
    response = Response(status_code=200)
    response.headers["X-Auth-User"] = principal.email
    response.headers["X-Auth-Org"] = str(org_id)
    return response


@router.post("/grafana/session")
async def grafana_session(payload: dict, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    await ensure_platform()
    user = await session.get(User, principal.user_id) if principal.token_kind == "session" else None
    email = user.email if user else principal.email
    name = user.full_name if user else email
    dashboard = str(payload.get("dashboard") or "hmc-overview")
    if principal.role in {"super_admin", "noc_operator"}:
        role = "Admin" if principal.role == "super_admin" else "Editor"
        await ensure_user(email, name, 1, role)
        return {"org_id": 1, "dashboard": dashboard}
    requested = payload.get("tenant_id")
    if requested and UUID(str(requested)) not in principal.tenant_ids:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    tenant_id = UUID(str(requested)) if requested else next(iter(principal.tenant_ids))
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    org_id = await ensure_tenant_org(str(tenant.id), f"hmc-{tenant.slug}")
    await ensure_user(email, name, org_id, "Viewer")
    return {"org_id": org_id, "dashboard": dashboard}
