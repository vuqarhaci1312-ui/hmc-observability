"""Create Grafana datasources, orgs, and dashboards with the admin API."""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote, urlparse

import httpx

from app.core.config import get_settings
from app.integrations.grafana_dashboards import PG, platform_dashboards, tenant_dashboards

logger = logging.getLogger(__name__)
_platform_ready = False
_tenant_orgs: dict[str, int] = {}


def _pg() -> dict:
    raw = get_settings().sync_database_url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(raw)
    return {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "postgres",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
    }


def _auth() -> tuple[str, str]:
    settings = get_settings()
    return settings.grafana_admin_user, settings.grafana_admin_password


def _timescale_body() -> dict:
    pg = _pg()
    return {
        "name": "TimescaleDB",
        "uid": PG["uid"],
        "type": PG["type"],
        "access": "proxy",
        "url": f"{pg['host']}:{pg['port']}",
        "user": pg["user"],
        "database": pg["database"],
        "isDefault": True,
        "jsonData": {
            "sslmode": os.environ.get("GRAFANA_SSLMODE", "require" if pg["host"].endswith(".render.com") else "disable"),
            "postgresVersion": 1600,
            "timescaledb": os.environ.get("GRAFANA_TIMESCALE", "true").lower() in {"1", "true", "yes"},
            "maxOpenConns": 5,
        },
        "secureJsonData": {"password": pg["password"]},
    }


def _loki_body(org_header: str, direct: bool) -> dict:
    settings = get_settings()
    if direct:
        return {
            "name": "Loki",
            "uid": "loki",
            "type": "loki",
            "access": "proxy",
            "url": settings.loki_url,
            "jsonData": {"httpHeaderName1": "X-Scope-OrgID"},
            "secureJsonData": {"httpHeaderValue1": org_header},
        }
    return {
        "name": "Loki",
        "uid": "loki",
        "type": "loki",
        "access": "proxy",
        "url": "http://api:8000/internal",
        "jsonData": {"httpHeaderName1": "X-HMC-Internal", "httpHeaderName2": "X-Scope-OrgID"},
        "secureJsonData": {"httpHeaderValue1": settings.secret_key, "httpHeaderValue2": org_header},
    }


async def _upsert_datasource(client: httpx.AsyncClient, org_id: int, body: dict) -> None:
    headers = {"X-Grafana-Org-Id": str(org_id)}
    existing = await client.get(f"/api/datasources/uid/{body['uid']}", headers=headers)
    if existing.status_code == 200:
        payload = {**body, "id": existing.json()["id"]}
        response = await client.put(f"/api/datasources/{payload['id']}", json=payload, headers=headers)
    else:
        response = await client.post("/api/datasources", json=body, headers=headers)
    if response.status_code >= 300:
        raise RuntimeError(f"Grafana datasource {body['uid']} failed: {response.status_code} {response.text[:300]}")


async def _upsert_dashboard(client: httpx.AsyncClient, org_id: int, dashboard: dict) -> None:
    dashboard = {**dashboard, "id": None}
    response = await client.post(
        "/api/dashboards/db",
        json={"dashboard": dashboard, "overwrite": True, "message": "hmc provision"},
        headers={"X-Grafana-Org-Id": str(org_id)},
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Grafana dashboard {dashboard.get('uid')} failed: {response.status_code} {response.text[:400]}")


async def ensure_platform() -> None:
    global _platform_ready
    if _platform_ready:
        return
    settings = get_settings()
    if not settings.grafana_admin_password:
        raise RuntimeError("GRAFANA_ADMIN_PASSWORD is empty")
    async with httpx.AsyncClient(base_url=settings.grafana_url, auth=_auth(), timeout=20) as client:
        await _upsert_datasource(client, 1, _timescale_body())
        await _upsert_datasource(client, 1, _loki_body("all", direct=False))
        for dashboard in platform_dashboards():
            await _upsert_dashboard(client, 1, dashboard)
    _platform_ready = True


async def ensure_tenant_org(tenant_id: str, tenant_name: str) -> int:
    if tenant_id in _tenant_orgs:
        return _tenant_orgs[tenant_id]
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.grafana_url, auth=_auth(), timeout=30) as client:
        found = await client.get(f"/api/orgs/name/{tenant_name}")
        if found.status_code == 200:
            org_id = int(found.json()["id"])
        else:
            created = await client.post("/api/orgs", json={"name": tenant_name})
            if created.status_code >= 300:
                raise RuntimeError(created.text[:300])
            org_id = int(created.json()["orgId"])
        await _upsert_datasource(client, org_id, _timescale_body())
        await _upsert_datasource(client, org_id, _loki_body(tenant_id, direct=True))
        for dashboard in tenant_dashboards(tenant_id):
            await _upsert_dashboard(client, org_id, dashboard)
        _tenant_orgs[tenant_id] = org_id
        return org_id


async def ensure_user(email: str, name: str, org_id: int, role: str) -> int:
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.grafana_url, auth=_auth(), timeout=20) as client:
        lookup = await client.get("/api/users/lookup", params={"loginOrEmail": email})
        if lookup.status_code == 404:
            created = await client.post(
                "/api/admin/users",
                json={"name": name, "email": email, "login": email, "password": settings.secret_key[:24], "OrgId": org_id},
            )
            if created.status_code >= 300 and created.status_code != 412:
                raise RuntimeError(created.text[:300])
            lookup = await client.get("/api/users/lookup", params={"loginOrEmail": email})
        user_id = int(lookup.json()["id"])
        added = await client.post(f"/api/orgs/{org_id}/users", json={"loginOrEmail": email, "role": role})
        if added.status_code >= 300 and "already" not in added.text.lower():
            logger.warning("grafana org membership: %s", added.text[:200])
        await client.patch(f"/api/orgs/{org_id}/users/{user_id}", json={"role": role})
        if org_id != 1 and role == "Viewer":
            await client.delete(f"/api/orgs/1/users/{user_id}")
        return user_id
