from __future__ import annotations

from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import AuthorizationError, Principal, require_permission, tenant_filter
from app.core.security import decode_token, hash_token
from app.database.session import get_session
from app.models.entities import ApiToken, User, UserTenantMembership

bearer = HTTPBearer(auto_error=False)


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    token = credentials.credentials if credentials else request.cookies.get("hmc_access")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    if token.startswith("hmc_"):
        stored = await session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token), ApiToken.revoked_at.is_(None)))
        if stored is None:
            raise HTTPException(status_code=401, detail="Invalid API token")
        return Principal(user_id=stored.id, email=stored.name, role=stored.role, tenant_ids={stored.tenant_id} if stored.tenant_id else set(), token_kind="api_token")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Invalid session")
    user = await session.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    memberships = list((await session.scalars(select(UserTenantMembership.tenant_id).where(UserTenantMembership.user_id == user.id))).all())
    return Principal(user_id=user.id, email=user.email, role=user.role, tenant_ids=set(memberships), token_kind="session")


def guard(permission: str):
    async def checker(principal: Principal = Depends(get_principal)) -> Principal:
        try:
            require_permission(principal, permission)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=exc.message) from exc
        return principal

    return checker


def scoped_tenants(principal: Principal, tenant_id: UUID | None) -> set[UUID] | None:
    try:
        return tenant_filter(principal, tenant_id)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
