from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, guard, scoped_tenants
from app.auth.rbac import Principal, source_types_for_mode
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, hash_token, new_token, verify_password
from app.database.session import get_redis, get_session
from app.ingestion.pipeline import EVENT_CHANNEL, EVENT_STREAM, LOG_STREAM, METRIC_STREAM, enqueue, publish_event
from app.models.entities import (
    AlertEvent,
    AlertRule,
    AuditEvent,
    Cluster,
    Datacenter,
    Host,
    Incident,
    IncidentComment,
    IncidentEvent,
    InfrastructureRelationship,
    IntegrationSetting,
    MetricDefinition,
    NotificationRecord,
    Region,
    ScenarioDefinition,
    ScenarioEvent,
    ScenarioRun,
    Service,
    Session,
    SimulationJob,
    SourceHealth,
    SystemSetting,
    TelemetrySource,
    Tenant,
    User,
    UserTenantMembership,
)
from app.services.control import acknowledge_alert, add_rule, incident_action, patch_rule, set_scenario_status, start_scenario
from app.services.read import latest_metrics, metric_query, overview, search_logs, tenant_health
from app.simulation.profiles import PROFILES, estimate_profile

router = APIRouter()


class LoginIn(BaseModel):
    email: str
    password: str


class MetricIn(BaseModel):
    timestamp: datetime
    tenant_id: UUID
    host_id: UUID
    service_id: UUID | None = None
    metric_name: str
    value: float
    unit: str | None = None
    source_type: str = "real"
    scenario_run_id: UUID | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    quality: str = "good"


class LogIn(BaseModel):
    timestamp: datetime
    tenant_id: UUID
    host_id: UUID
    hostname: str
    service_id: UUID | None = None
    service: str = "host"
    source_type: str = "real"
    environment: str = "production"
    severity: str
    component: str
    event_type: str
    message: str
    trace_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    scenario_run_id: UUID | None = None
    attributes: dict = Field(default_factory=dict)


class IngestMetrics(BaseModel):
    items: list[MetricIn]


class IngestLogs(BaseModel):
    items: list[LogIn]


class IngestEvents(BaseModel):
    items: list[dict]


def _cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    response.set_cookie("hmc_access", access, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.access_token_minutes * 60, path="/")
    response.set_cookie("hmc_refresh", refresh, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.refresh_token_days * 86400, path="/api/v1/auth")


async def _rate_limit(key: str) -> None:
    redis = get_redis()
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, 60)
    if current > get_settings().rate_limit_per_minute:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def _mode_types(mode: str) -> list[str] | None:
    try:
        return source_types_for_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    await _rate_limit(f"login:{request.client.host if request.client else 'local'}")
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    refresh = new_token()
    access = create_access_token(user.id, user.role)
    session.add(Session(user_id=user.id, refresh_token_hash=hash_token(refresh), expires_at=datetime.now(timezone.utc) + timedelta(days=get_settings().refresh_token_days), user_agent=request.headers.get("user-agent"), ip_address=request.client.host if request.client else None))
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    _cookies(response, access, refresh)
    return {"access_token": access, "token_type": "bearer", "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "role": user.role}}


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    raw = request.cookies.get("hmc_refresh")
    if not raw:
        raise HTTPException(status_code=401, detail="Refresh token missing")
    stored = await session.scalar(select(Session).where(Session.refresh_token_hash == hash_token(raw), Session.revoked_at.is_(None)))
    if stored is None or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")
    user = await session.get(User, stored.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User missing")
    stored.revoked_at = datetime.now(timezone.utc)
    replacement = new_token()
    access = create_access_token(user.id, user.role)
    session.add(Session(user_id=user.id, refresh_token_hash=hash_token(replacement), expires_at=datetime.now(timezone.utc) + timedelta(days=get_settings().refresh_token_days), user_agent=stored.user_agent, ip_address=stored.ip_address))
    await session.commit()
    _cookies(response, access, replacement)
    return {"access_token": access, "token_type": "bearer"}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    raw = request.cookies.get("hmc_refresh")
    if raw:
        stored = await session.scalar(select(Session).where(Session.refresh_token_hash == hash_token(raw)))
        if stored:
            stored.revoked_at = datetime.now(timezone.utc)
            await session.commit()
    response.delete_cookie("hmc_access", path="/")
    response.delete_cookie("hmc_refresh", path="/api/v1/auth")
    return {"status": "logged_out"}


@router.get("/auth/me")
async def me(principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    user = await session.get(User, principal.user_id) if principal.token_kind == "session" else None
    return {"id": str(principal.user_id), "email": user.email if user else principal.email, "full_name": user.full_name if user else principal.email, "role": principal.role, "tenant_ids": [str(item) for item in principal.tenant_ids]}


@router.get("/overview/summary")
async def overview_summary(mode: str = "demo", tenant_id: UUID | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    scoped_tenants(principal, tenant_id)
    data = await overview(session, principal, mode, tenant_id)
    redis = get_redis()
    depth = await redis.xlen(METRIC_STREAM)
    data["ingestion_queue_depth"] = depth
    data["ingestion_rate_per_sec"] = None
    rate = await redis.get("hmc:ingest_rate")
    if rate:
        data["ingestion_rate_per_sec"] = float(rate)
    return data


@router.get("/overview/health")
async def overview_health(mode: str = "demo", tenant_id: UUID | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    summary = await overview(session, principal, mode, tenant_id)
    return {"health": summary, "source": "postgresql"}


@router.get("/overview/activity")
async def overview_activity(tenant_id: UUID | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    stmt = select(IncidentEvent).order_by(IncidentEvent.created_at.desc()).limit(40)
    events = list((await session.scalars(stmt)).all())
    alerts = list((await session.scalars(select(AlertEvent).order_by(AlertEvent.started_at.desc()).limit(40))).all())
    if allowed is not None:
        alerts = [item for item in alerts if item.tenant_id in allowed]
    return {
        "incidents": [{"id": str(item.id), "kind": item.kind, "message": item.message, "at": item.created_at.isoformat()} for item in events],
        "alerts": [{"id": str(item.id), "title": item.title, "state": item.state, "severity": item.severity, "at": item.started_at.isoformat(), "tenant_id": str(item.tenant_id)} for item in alerts],
        "source": "postgresql",
    }


@router.get("/tenants")
async def tenants(mode: str = "demo", q: str | None = None, page: int = 1, page_size: int = 25, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, None)
    stmt = select(Tenant)
    types = _mode_types(mode)
    if types:
        stmt = stmt.where(Tenant.source_type.in_(types))
    if allowed is not None:
        stmt = stmt.where(Tenant.id.in_(allowed)) if allowed else stmt.where(text_false())
    if q:
        stmt = stmt.where(Tenant.name.ilike(f"%{q}%"))
    rows = list((await session.scalars(stmt.order_by(Tenant.name).offset((page - 1) * page_size).limit(page_size))).all())
    items = []
    for tenant in rows:
        health = await tenant_health(session, tenant.id)
        hosts = await session.scalar(select(func.count()).select_from(Host).where(Host.tenant_id == tenant.id)) or 0
        services = await session.scalar(select(func.count()).select_from(Service).where(Service.tenant_id == tenant.id)) or 0
        last_seen = await session.scalar(select(func.max(Host.last_seen_at)).where(Host.tenant_id == tenant.id))
        items.append({"id": str(tenant.id), "name": tenant.name, "environment": tenant.environment, "hosts": hosts, "services": services, "health": health, "active_alerts": health["firing_alerts"], "active_incidents": health["open_critical_incidents"], "last_telemetry": last_seen.isoformat() if last_seen else None, "sla": tenant.sla_target, "source_type": tenant.source_type})
    return {"items": items, "page": page, "page_size": page_size, "no_connected_sources": mode == "real" and not items, "source": "postgresql"}


def text_false():
    from sqlalchemy import text
    return text("false")


@router.get("/tenants/{tenant_id}")
async def tenant_detail(tenant_id: UUID, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    scoped_tenants(principal, tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    health = await tenant_health(session, tenant_id)
    return {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug, "profile": tenant.profile, "environment": tenant.environment, "source_type": tenant.source_type, "sla_target": tenant.sla_target, "health": health, "source": "postgresql"}


@router.get("/tenants/{tenant_id}/summary")
async def tenant_summary(tenant_id: UUID, mode: str = "demo", start: datetime | None = None, end: datetime | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    scoped_tenants(principal, tenant_id)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=6)
    health = await tenant_health(session, tenant_id)
    cpu = await metric_query(session, metric="cpu_usage_percent", start=start, end=end, tenant_ids={tenant_id}, host_id=None, source_types=_mode_types(mode))
    memory = await metric_query(session, metric="memory_usage_percent", start=start, end=end, tenant_ids={tenant_id}, host_id=None, source_types=_mode_types(mode))
    return {"health": health, "cpu": cpu, "memory": memory, "source": "timescaledb"}


@router.get("/hosts")
async def hosts(mode: str = "demo", tenant_id: UUID | None = None, q: str | None = None, page: int = 1, page_size: int = 50, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    stmt = select(Host)
    types = _mode_types(mode)
    if types:
        stmt = stmt.where(Host.source_type.in_(types))
    if allowed is not None:
        stmt = stmt.where(or_(Host.tenant_id.in_(allowed), Host.tenant_id.is_(None))) if allowed else stmt.where(text_false())
    if q:
        stmt = stmt.where(Host.hostname.ilike(f"%{q}%"))
    rows = list((await session.scalars(stmt.order_by(Host.hostname).offset((page - 1) * page_size).limit(min(page_size, 100)))).all())
    return {"items": [_host(item) for item in rows], "page": page, "page_size": page_size, "no_connected_sources": mode == "real" and not rows, "source": "postgresql"}


def _host(item: Host) -> dict:
    return {"id": str(item.id), "hostname": item.hostname, "ip_address": item.ip_address, "os_name": item.os_name, "role": item.role, "host_kind": item.host_kind, "environment": item.environment, "tenant_id": str(item.tenant_id) if item.tenant_id else None, "source_type": item.source_type, "health_status": item.health_status, "lifecycle_status": item.lifecycle_status, "uptime_seconds": item.uptime_seconds, "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None, "cpu_cores": item.cpu_cores, "memory_bytes": item.memory_bytes, "disk_bytes": item.disk_bytes, "physical_host_id": str(item.physical_host_id) if item.physical_host_id else None, "status_source": "telemetry"}


@router.get("/hosts/{host_id}")
async def host_detail(host_id: UUID, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    scoped_tenants(principal, host.tenant_id)
    return _host(host)


@router.get("/hosts/{host_id}/metrics")
async def host_metrics(host_id: UUID, metric: str = "cpu_usage_percent", start: datetime | None = None, end: datetime | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    scoped_tenants(principal, host.tenant_id)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=6)
    return await metric_query(session, metric=metric, start=start, end=end, tenant_ids={host.tenant_id} if host.tenant_id else None, host_id=host_id, source_types=[host.source_type])


@router.get("/hosts/{host_id}/logs")
async def host_logs(host_id: UUID, start: datetime | None = None, end: datetime | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    host = await session.get(Host, host_id)
    if host is None or host.tenant_id is None:
        raise HTTPException(status_code=404, detail="Host not found")
    scoped_tenants(principal, host.tenant_id)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=6)
    return await search_logs({"start": start, "end": end, "host": host.hostname, "limit": 100}, [str(host.tenant_id)])


@router.get("/hosts/{host_id}/services")
async def host_services(host_id: UUID, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    scoped_tenants(principal, host.tenant_id)
    rows = list((await session.scalars(select(Service).where(Service.host_id == host_id))).all())
    return {"items": [_service(item) for item in rows], "source": "postgresql"}


def _service(item: Service) -> dict:
    return {"id": str(item.id), "name": item.name, "kind": item.kind, "component": item.component, "host_id": str(item.host_id), "tenant_id": str(item.tenant_id) if item.tenant_id else None, "environment": item.environment, "source_type": item.source_type, "health_status": item.health_status, "port": item.port}


@router.get("/services")
async def services(mode: str = "demo", tenant_id: UUID | None = None, kind: str | None = None, page: int = 1, page_size: int = 50, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    stmt = select(Service)
    types = _mode_types(mode)
    if types:
        stmt = stmt.where(Service.source_type.in_(types))
    if allowed is not None:
        stmt = stmt.where(Service.tenant_id.in_(allowed)) if allowed else stmt.where(text_false())
    if kind:
        stmt = stmt.where(Service.kind == kind)
    rows = list((await session.scalars(stmt.offset((page - 1) * page_size).limit(min(page_size, 100)))).all())
    return {"items": [_service(item) for item in rows], "page": page, "page_size": page_size, "no_connected_sources": mode == "real" and not rows, "source": "postgresql"}


@router.get("/services/{service_id}")
async def service_detail(service_id: UUID, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    item = await session.get(Service, service_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Service not found")
    scoped_tenants(principal, item.tenant_id)
    return _service(item)


@router.get("/services/{service_id}/health")
async def service_health(service_id: UUID, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    item = await session.get(Service, service_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Service not found")
    scoped_tenants(principal, item.tenant_id)
    host = await session.get(Host, item.host_id)
    return {"service": _service(item), "host_health": host.health_status if host else "unknown", "source": "telemetry"}


@router.get("/logs/search")
async def logs_search(tenant_id: UUID | None = None, host: str | None = None, service: str | None = None, severity: str | None = None, source_type: str | None = None, environment: str | None = None, search: str | None = None, correlation_id: str | None = None, seed_id: str | None = None, pipeline: str | None = None, start: datetime | None = None, end: datetime | None = None, limit: int = 100, principal: Principal = Depends(guard("telemetry:read"))):
    allowed = scoped_tenants(principal, tenant_id)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=6)
    if allowed is None:
        raise HTTPException(status_code=422, detail="Select a tenant to search logs")
    filters = {"start": start, "end": end, "host": host, "service": service, "severity": severity, "source_type": source_type, "environment": environment, "search": search or correlation_id, "seed_id": seed_id, "pipeline": pipeline, "limit": limit}
    try:
        return await search_logs(filters, [str(item) for item in allowed])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/logs/labels")
async def log_labels(principal: Principal = Depends(guard("telemetry:read"))):
    return {"labels": ["tenant_id", "host", "service", "severity", "source_type", "environment", "component", "seed_id"], "source": "loki"}


@router.get("/logs/context")
async def log_context(tenant_id: UUID, timestamp: datetime, host: str | None = None, principal: Principal = Depends(guard("telemetry:read"))):
    scoped_tenants(principal, tenant_id)
    return await search_logs({"start": timestamp - timedelta(minutes=5), "end": timestamp + timedelta(minutes=5), "host": host, "limit": 40}, [str(tenant_id)])


@router.get("/metrics/catalog")
async def metrics_catalog(principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(MetricDefinition).order_by(MetricDefinition.name))).all())
    return {"items": [{"name": item.name, "unit": item.unit, "type": item.metric_type, "description": item.description} for item in rows], "source": "postgresql"}


@router.get("/metrics/query")
async def metrics_query(metric: str, start: datetime, end: datetime, mode: str = "demo", tenant_id: UUID | None = None, host_id: UUID | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    return await metric_query(session, metric=metric, start=start, end=end, tenant_ids=allowed, host_id=host_id, source_types=_mode_types(mode))


@router.get("/metrics/series")
async def metrics_series(metric: str, start: datetime, end: datetime, mode: str = "demo", tenant_id: UUID | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    return await metrics_query(metric, start, end, mode, tenant_id, None, principal, session)


@router.get("/alerts")
async def alerts(state: str | None = None, tenant_id: UUID | None = None, principal: Principal = Depends(guard("alerts:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    stmt = select(AlertEvent).order_by(AlertEvent.started_at.desc()).limit(200)
    if allowed is not None:
        stmt = stmt.where(AlertEvent.tenant_id.in_(allowed)) if allowed else stmt.where(text_false())
    if state:
        stmt = stmt.where(AlertEvent.state == state)
    rows = list((await session.scalars(stmt)).all())
    return {"items": [_alert(item) for item in rows], "source": "postgresql"}


def _alert(item: AlertEvent) -> dict:
    return {"id": str(item.id), "title": item.title, "state": item.state, "severity": item.severity, "tenant_id": str(item.tenant_id), "host_id": str(item.host_id), "rule": (item.details or {}).get("rule"), "last_value": item.last_value, "started_at": item.started_at.isoformat(), "firing_at": item.firing_at.isoformat() if item.firing_at else None, "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None, "scenario_run_id": str(item.scenario_run_id) if item.scenario_run_id else None}


@router.get("/alert-rules")
async def alert_rules(principal: Principal = Depends(guard("alerts:read")), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(AlertRule).order_by(AlertRule.code))).all())
    return {"items": [{"id": str(item.id), "code": item.code, "rule_type": item.rule_type, "metric_name": item.metric_name, "comparator": item.comparator, "threshold": item.threshold, "for_seconds": item.for_seconds, "resolve_seconds": item.resolve_seconds, "hysteresis": item.hysteresis, "cooldown_seconds": item.cooldown_seconds, "severity": item.severity, "enabled": item.enabled} for item in rows]}


@router.post("/alert-rules")
async def create_rule(payload: dict, principal: Principal = Depends(guard("alerts:write")), session: AsyncSession = Depends(get_session)):
    rule = await add_rule(session, payload)
    return {"id": str(rule.id), "code": rule.code}


@router.patch("/alert-rules/{rule_id}")
async def update_rule(rule_id: UUID, payload: dict, principal: Principal = Depends(guard("alerts:write")), session: AsyncSession = Depends(get_session)):
    rule = await patch_rule(session, rule_id, payload)
    return {"id": str(rule.id), "version": rule.version}


@router.post("/alerts/{alert_id}/acknowledge")
async def ack_alert(alert_id: UUID, principal: Principal = Depends(guard("alerts:write")), session: AsyncSession = Depends(get_session)):
    alert = await acknowledge_alert(session, alert_id, principal.email)
    scoped_tenants(principal, alert.tenant_id)
    return _alert(alert)


@router.get("/incidents")
async def incidents(status: str | None = None, tenant_id: UUID | None = None, principal: Principal = Depends(guard("incidents:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    stmt = select(Incident).order_by(Incident.started_at.desc()).limit(200)
    if allowed is not None:
        stmt = stmt.where(Incident.tenant_id.in_(allowed)) if allowed else stmt.where(text_false())
    if status:
        stmt = stmt.where(Incident.status == status)
    rows = list((await session.scalars(stmt)).all())
    return {"items": [_incident(item) for item in rows], "source": "postgresql"}


def _incident(item: Incident) -> dict:
    duration = None
    if item.resolved_at:
        duration = (item.resolved_at - item.started_at).total_seconds()
    return {"id": str(item.id), "title": item.title, "severity": item.severity, "priority": item.priority, "status": item.status, "tenant_id": str(item.tenant_id), "affected_tenant_ids": item.affected_tenant_ids, "affected_host_ids": item.affected_host_ids, "affected_service_ids": item.affected_service_ids, "started_at": item.started_at.isoformat(), "detected_at": item.detected_at.isoformat(), "acknowledged_at": item.acknowledged_at.isoformat() if item.acknowledged_at else None, "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None, "duration_seconds": duration, "assigned_user_id": str(item.assigned_user_id) if item.assigned_user_id else None, "hypothesis": item.hypothesis, "cause_confirmed": item.cause_confirmed, "remediation": item.remediation, "correlation_key": item.correlation_key, "scenario_run_id": str(item.scenario_run_id) if item.scenario_run_id else None, "source_type": item.source_type}


@router.get("/incidents/{incident_id}")
async def incident_detail(incident_id: UUID, principal: Principal = Depends(guard("incidents:read")), session: AsyncSession = Depends(get_session)):
    item = await session.get(Incident, incident_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    scoped_tenants(principal, item.tenant_id)
    events = list((await session.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.created_at))).all())
    comments = list((await session.scalars(select(IncidentComment).where(IncidentComment.incident_id == incident_id))).all())
    return {**_incident(item), "timeline": [{"kind": event.kind, "message": event.message, "actor": event.actor, "at": event.created_at.isoformat()} for event in events], "comments": [{"body": comment.body, "at": comment.created_at.isoformat()} for comment in comments]}


async def _incident_route(incident_id: UUID, action: str, payload: dict, principal: Principal, session: AsyncSession):
    try:
        item = await incident_action(session, incident_id, action, await session.get(User, principal.user_id), payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    scoped_tenants(principal, item.tenant_id)
    await publish_event({"type": "incident_updated", "tenant_ids": [str(item.tenant_id)], "incident_id": str(item.id), "status": item.status})
    return _incident(item)


@router.post("/incidents/{incident_id}/acknowledge")
async def incident_ack(incident_id: UUID, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "acknowledge", {}, principal, session)


@router.post("/incidents/{incident_id}/assign")
async def incident_assign(incident_id: UUID, payload: dict, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "assign", payload, principal, session)


@router.post("/incidents/{incident_id}/comments")
async def incident_comment(incident_id: UUID, payload: dict, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "comment", payload, principal, session)


@router.post("/incidents/{incident_id}/resolve")
async def incident_resolve(incident_id: UUID, payload: dict | None = None, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "resolve", payload or {}, principal, session)


@router.post("/incidents/{incident_id}/reopen")
async def incident_reopen(incident_id: UUID, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "reopen", {}, principal, session)


@router.post("/incidents/{incident_id}/close")
async def incident_close(incident_id: UUID, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "close", {}, principal, session)


@router.post("/incidents/{incident_id}/priority")
async def incident_priority(incident_id: UUID, payload: dict, principal: Principal = Depends(guard("incidents:write")), session: AsyncSession = Depends(get_session)):
    return await _incident_route(incident_id, "priority", payload, principal, session)


@router.get("/scenarios")
async def scenarios(principal: Principal = Depends(guard("scenarios:read")), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(ScenarioDefinition).order_by(ScenarioDefinition.code))).all())
    return {"items": [{"code": item.code, "name": item.name, "description": item.description, "category": item.category, "severity": item.severity, "infrastructure_type": item.infrastructure_type, "duration_seconds": item.duration_seconds, "expected_alerts": item.expected_alerts, "expected_incidents": item.expected_incidents, "recovery": item.recovery, "symptoms": (item.definition or {}).get("symptoms", [])} for item in rows]}


@router.post("/scenarios/run")
async def run_scenario(payload: dict, principal: Principal = Depends(guard("scenarios:write")), session: AsyncSession = Depends(get_session)):
    tenant_id = UUID(payload["tenant_id"]) if payload.get("tenant_id") else None
    scoped_tenants(principal, tenant_id)
    try:
        run = await start_scenario(session, code=payload["code"], tenant_id=tenant_id, host_id=UUID(payload["host_id"]) if payload.get("host_id") else None, speed=float(payload.get("speed") or 1), seed=int(payload.get("seed") or 1))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await publish_event({"type": "scenario_started", "tenant_ids": [str(run.tenant_id)] if run.tenant_id else [], "run_id": str(run.id)})
    return {"id": str(run.id), "status": run.status, "phase": run.phase}


@router.get("/scenario-runs")
async def scenario_runs(principal: Principal = Depends(guard("scenarios:read")), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(ScenarioRun).order_by(ScenarioRun.started_at.desc()).limit(100))).all())
    return {"items": [_run(item) for item in rows]}


def _run(item: ScenarioRun) -> dict:
    real_elapsed = None
    if item.started_at:
        end = item.ended_at or datetime.now(timezone.utc)
        real_elapsed = (end - item.started_at).total_seconds() - float((item.evaluation or {}).get("paused_seconds") or 0)
    return {"id": str(item.id), "status": item.status, "phase": item.phase, "speed": item.speed, "intensity": item.intensity, "tenant_id": str(item.tenant_id) if item.tenant_id else None, "host_ids": item.host_ids, "service_ids": item.service_ids, "started_at": item.started_at.isoformat() if item.started_at else None, "ended_at": item.ended_at.isoformat() if item.ended_at else None, "virtual_elapsed_seconds": item.virtual_elapsed_seconds, "real_elapsed_seconds": real_elapsed, "logs_generated": item.logs_generated, "metrics_generated": item.metrics_generated, "alerts_triggered": item.alerts_triggered, "incidents_created": item.incidents_created, "evaluation": item.evaluation}


@router.get("/scenario-runs/{run_id}")
async def scenario_run_detail(run_id: UUID, principal: Principal = Depends(guard("scenarios:read")), session: AsyncSession = Depends(get_session)):
    item = await session.get(ScenarioRun, run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Scenario run not found")
    scoped_tenants(principal, item.tenant_id)
    events = list((await session.scalars(select(ScenarioEvent).where(ScenarioEvent.run_id == run_id).order_by(ScenarioEvent.created_at))).all())
    return {**_run(item), "events": [{"phase": event.phase, "message": event.message, "at": event.created_at.isoformat()} for event in events]}


async def _scenario_status(run_id: UUID, status: str, principal: Principal, session: AsyncSession):
    run = await set_scenario_status(session, run_id, status)
    scoped_tenants(principal, run.tenant_id)
    await publish_event({"type": "scenario_finished" if status in {"stopped", "finished"} else "scenario_progress", "tenant_ids": [str(run.tenant_id)] if run.tenant_id else [], "run_id": str(run.id), "status": status})
    return _run(run)


@router.post("/scenario-runs/{run_id}/pause")
async def pause_run(run_id: UUID, principal: Principal = Depends(guard("scenarios:write")), session: AsyncSession = Depends(get_session)):
    return await _scenario_status(run_id, "paused", principal, session)


@router.post("/scenario-runs/{run_id}/resume")
async def resume_run(run_id: UUID, principal: Principal = Depends(guard("scenarios:write")), session: AsyncSession = Depends(get_session)):
    return await _scenario_status(run_id, "running", principal, session)


@router.post("/scenario-runs/{run_id}/stop")
async def stop_run(run_id: UUID, principal: Principal = Depends(guard("scenarios:write")), session: AsyncSession = Depends(get_session)):
    return await _scenario_status(run_id, "stopped", principal, session)


@router.post("/scenario-runs/{run_id}/replay")
async def replay_run(run_id: UUID, principal: Principal = Depends(guard("scenarios:write")), session: AsyncSession = Depends(get_session)):
    existing = await session.get(ScenarioRun, run_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Scenario run not found")
    definition = await session.get(ScenarioDefinition, existing.definition_id)
    return await run_scenario({"code": definition.code, "tenant_id": str(existing.tenant_id) if existing.tenant_id else None, "host_id": existing.host_ids[0] if existing.host_ids else None, "speed": existing.speed, "seed": existing.seed}, principal, session)


@router.get("/simulation/status")
async def simulation_status(principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    job = await session.scalar(select(SimulationJob).order_by(SimulationJob.started_at.desc()).limit(1))
    profiles = [estimate_profile(name) for name in PROFILES]
    return {"job": None if job is None else {"id": str(job.id), "profile": job.profile_name, "status": job.status, "seed": job.seed, "seed_id": job.seed_id, "progress": job.progress, "metrics_written": job.metrics_written, "logs_written": job.logs_written, "message": job.message}, "profiles": profiles}


@router.post("/simulation/start")
async def simulation_start(payload: dict, principal: Principal = Depends(guard("simulation:write")), session: AsyncSession = Depends(get_session)):
    from app.simulation.seed import seed_demo
    profile = payload.get("profile") or "quick"
    seed = int(payload.get("seed") or get_settings().demo_random_seed)
    return await seed_demo(profile, seed, reseed=bool(payload.get("reseed")))


@router.post("/simulation/pause")
async def simulation_pause(principal: Principal = Depends(guard("simulation:write")), session: AsyncSession = Depends(get_session)):
    return await _job_status(session, "paused")


@router.post("/simulation/resume")
async def simulation_resume(principal: Principal = Depends(guard("simulation:write")), session: AsyncSession = Depends(get_session)):
    return await _job_status(session, "running")


@router.post("/simulation/reset")
async def simulation_reset(principal: Principal = Depends(guard("simulation:write")), session: AsyncSession = Depends(get_session)):
    return await _job_status(session, "stopped")


async def _job_status(session: AsyncSession, status: str):
    job = await session.scalar(select(SimulationJob).order_by(SimulationJob.started_at.desc()).limit(1))
    if job is None:
        raise HTTPException(status_code=404, detail="Simulation job not found")
    job.status = status
    job.message = status
    await session.commit()
    return {"id": str(job.id), "status": job.status}


@router.get("/datasources")
async def datasources(principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    integrations = list((await session.scalars(select(IntegrationSetting))).all())
    sources = list((await session.scalars(select(TelemetrySource).limit(100))).all())
    return {"items": [{"provider": item.provider, "status": item.status, "message": item.message} for item in integrations] + [{"name": item.name, "kind": item.kind, "source_type": item.source_type, "status": item.status} for item in sources], "source": "postgresql"}


@router.get("/datasources/health")
async def datasource_health(principal: Principal = Depends(guard("telemetry:read"))):
    from app.services.platform_health import dependency_health
    return await dependency_health()


@router.get("/system/health")
async def system_health(principal: Principal = Depends(guard("telemetry:read"))):
    from app.services.platform_health import dependency_health
    return await dependency_health()


@router.get("/system/audit")
async def system_audit(principal: Principal = Depends(guard("audit:read")), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))).all())
    return {"items": [{"action": item.action, "actor": item.actor_email, "target_type": item.target_type, "target_id": item.target_id, "at": item.created_at.isoformat(), "details": item.details} for item in rows]}


@router.get("/topology")
async def topology(tenant_id: UUID | None = None, principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, tenant_id)
    stmt = select(InfrastructureRelationship)
    if allowed is not None:
        stmt = stmt.where(or_(InfrastructureRelationship.tenant_id.in_(allowed), InfrastructureRelationship.tenant_id.is_(None))) if allowed else stmt.where(text_false())
    rows = list((await session.scalars(stmt.limit(2000))).all())
    hosts = list((await session.scalars(select(Host))).all())
    if allowed is not None:
        hosts = [host for host in hosts if host.tenant_id in allowed or host.tenant_id is None]
    return {"nodes": [_host(host) for host in hosts], "edges": [{"from_type": item.from_type, "from_id": str(item.from_id), "to_type": item.to_type, "to_id": str(item.to_id), "relationship": item.relationship} for item in rows], "regions": [{"id": str(item.id), "name": item.name} for item in (await session.scalars(select(Region))).all()], "datacenters": [{"id": str(item.id), "name": item.name, "region_id": str(item.region_id)} for item in (await session.scalars(select(Datacenter))).all()], "clusters": [{"id": str(item.id), "name": item.name, "datacenter_id": str(item.datacenter_id)} for item in (await session.scalars(select(Cluster))).all()], "source": "postgresql"}


@router.get("/domain/{name}")
async def domain_summary(name: str, mode: str = "demo", tenant_id: UUID | None = None, principal: Principal = Depends(guard("telemetry:read")), session: AsyncSession = Depends(get_session)):
    metrics = {"telephony": ["sip_registered", "sip_active_calls", "sip_setup_success_ratio", "sip_jitter_ms", "sip_packet_loss_ratio"], "mail": ["mail_queue_size", "mail_delivery_rate", "mail_oldest_age_seconds", "mail_smtp_connections"], "cloud": ["cpu_usage_percent", "memory_usage_percent", "storage_latency_ms", "service_availability"]}.get(name)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Unknown domain")
    allowed = scoped_tenants(principal, tenant_id)
    rows = await latest_metrics(session, metrics, allowed, _mode_types(mode))
    return {"domain": name, "metrics": rows, "no_connected_sources": mode == "real" and not rows, "source": "timescaledb"}


@router.get("/search")
async def search(q: str = Query(min_length=1), principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    allowed = scoped_tenants(principal, None)
    tenant_stmt = select(Tenant).where(Tenant.name.ilike(f"%{q}%")).limit(10)
    host_stmt = select(Host).where(Host.hostname.ilike(f"%{q}%")).limit(10)
    if allowed is not None:
        tenant_stmt = tenant_stmt.where(Tenant.id.in_(allowed)) if allowed else tenant_stmt.where(text_false())
        host_stmt = host_stmt.where(Host.tenant_id.in_(allowed)) if allowed else host_stmt.where(text_false())
    return {"tenants": [{"id": str(item.id), "name": item.name} for item in (await session.scalars(tenant_stmt)).all()], "hosts": [{"id": str(item.id), "hostname": item.hostname} for item in (await session.scalars(host_stmt)).all()]}


@router.get("/notifications")
async def notifications(principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(NotificationRecord).order_by(NotificationRecord.created_at.desc()).limit(50))).all())
    return {"items": [{"id": str(item.id), "title": item.title, "body": item.body, "kind": item.kind, "link": item.link, "read": item.read_at is not None, "at": item.created_at.isoformat()} for item in rows]}


@router.get("/admin/users")
async def admin_users(principal: Principal = Depends(guard("settings:write")), session: AsyncSession = Depends(get_session)):
    if principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    rows = list((await session.scalars(select(User).order_by(User.email))).all())
    return {"items": [{"id": str(item.id), "email": item.email, "full_name": item.full_name, "role": item.role, "is_active": item.is_active} for item in rows]}


@router.post("/admin/users")
async def create_user(payload: dict, principal: Principal = Depends(guard("settings:write")), session: AsyncSession = Depends(get_session)):
    if principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    user = User(email=payload["email"], full_name=payload["full_name"], password_hash=hash_password(payload["password"]), role=payload["role"], is_active=True)
    session.add(user)
    await session.flush()
    for tenant_id in payload.get("tenant_ids") or []:
        session.add(UserTenantMembership(user_id=user.id, tenant_id=UUID(tenant_id), role=payload["role"]))
    await session.commit()
    return {"id": str(user.id)}


@router.post("/admin/api-tokens")
async def create_token(payload: dict, principal: Principal = Depends(guard("settings:write")), session: AsyncSession = Depends(get_session)):
    if principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    raw = "hmc_" + new_token(24)
    from app.models.entities import ApiToken
    token = ApiToken(name=payload["name"], token_hash=hash_token(raw), token_prefix=raw[:12], tenant_id=UUID(payload["tenant_id"]) if payload.get("tenant_id") else None, role=payload.get("role") or "tenant_viewer")
    session.add(token)
    await session.commit()
    return {"token": raw, "prefix": token.token_prefix, "note": "Store this token now. It is not shown again."}


@router.get("/system/settings")
async def get_settings_route(principal: Principal = Depends(guard("inventory:read")), session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(SystemSetting))).all())
    return {"items": [{"key": item.key, "value": item.value} for item in rows]}


@router.get("/events/stream")
async def event_stream(request: Request, principal: Principal = Depends(get_principal)):
    redis = get_redis()

    async def generate():
        yield "retry: 2000\n\n"
        pubsub = redis.pubsub()
        await pubsub.subscribe(EVENT_CHANNEL)
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if not message or message.get("type") != "message":
                    yield ": keepalive\n\n"
                    continue
                raw = message["data"]
                data = json.loads(raw)
                tenants = set(data.get("tenant_ids") or [])
                if principal.role != "super_admin" and tenants and tenants.isdisjoint({str(item) for item in principal.tenant_ids}):
                    continue
                yield f"id: {data.get('id')}\ndata: {json.dumps(data)}\n\n"
        finally:
            await pubsub.unsubscribe(EVENT_CHANNEL)
            await pubsub.aclose()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _ingest(kind: str, stream: str, items: list[dict], principal: Principal, request: Request, source_type: str):
    settings = get_settings()
    if len(items) > settings.ingest_max_batch:
        raise HTTPException(status_code=413, detail="Batch too large")
    await _rate_limit(f"ingest:{principal.user_id}")
    key = request.headers.get("idempotency-key")
    redis = get_redis()
    if key:
        accepted = await redis.set(f"idem:{key}", "1", nx=True, ex=86400)
        if not accepted:
            return {"status": "duplicate", "idempotency_key": key}
    for item in items:
        tenant_id = UUID(str(item["tenant_id"])) if item.get("tenant_id") else None
        if tenant_id:
            scoped_tenants(principal, tenant_id)
        item["source_type"] = source_type if principal.token_kind == "api_token" else item.get("source_type") or source_type
    await enqueue(stream, {"kind": kind, "items": items, "retries": 0})
    return {"status": "queued", "count": len(items)}


@router.post("/ingest/metrics", status_code=202)
async def ingest_metrics(body: IngestMetrics, request: Request, principal: Principal = Depends(guard("telemetry:read"))):
    if principal.token_kind != "api_token" and principal.role not in {"super_admin", "noc_operator"}:
        raise HTTPException(status_code=403, detail="Agent token required")
    return await _ingest("metrics", METRIC_STREAM, [item.model_dump(mode="json") for item in body.items], principal, request, "real")


@router.post("/ingest/logs", status_code=202)
async def ingest_logs(body: IngestLogs, request: Request, principal: Principal = Depends(guard("telemetry:read"))):
    if principal.token_kind != "api_token" and principal.role not in {"super_admin", "noc_operator"}:
        raise HTTPException(status_code=403, detail="Agent token required")
    return await _ingest("logs", LOG_STREAM, [item.model_dump(mode="json") for item in body.items], principal, request, "real")


@router.post("/ingest/events", status_code=202)
async def ingest_events(body: IngestEvents, request: Request, principal: Principal = Depends(guard("telemetry:read"))):
    return await _ingest("events", "ingest:events", body.items, principal, request, "real")


@router.post("/ingest/loki/push", status_code=202)
async def ingest_loki(payload: dict, request: Request, principal: Principal = Depends(get_principal)):
    if principal.token_kind != "api_token":
        raise HTTPException(status_code=403, detail="Agent token required")
    tenant_id = next(iter(principal.tenant_ids), None)
    if tenant_id is None:
        raise HTTPException(status_code=422, detail="API token is not bound to a tenant")
    logs = []
    for stream in payload.get("streams") or []:
        labels = stream.get("stream") or {}
        for ts, line in stream.get("values") or []:
            moment = datetime.fromtimestamp(int(ts) / 1e9, timezone.utc)
            logs.append({"timestamp": moment.isoformat(), "tenant_id": str(tenant_id), "host_id": labels.get("host_id") or "00000000-0000-0000-0000-000000000000", "hostname": labels.get("host") or "unknown", "service": labels.get("service") or "host", "source_type": "real", "environment": labels.get("environment") or "production", "severity": labels.get("severity") or "INFO", "component": labels.get("component") or "agent", "event_type": "forwarded", "message": line, "attributes": {}})
    return await _ingest("logs", LOG_STREAM, logs, principal, request, "real")
