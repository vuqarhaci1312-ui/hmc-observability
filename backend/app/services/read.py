from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal, source_types_for_mode
from app.core.config import get_settings
from app.core.healthscore import health_score, status_from_score
from app.models.entities import (
    AlertEvent,
    AlertRule,
    Host,
    Incident,
    Service,
    Tenant,
)


def _types(mode: str) -> list[str] | None:
    return source_types_for_mode(mode)


def bucket_for(start: datetime, end: datetime) -> str:
    span = end - start
    if span <= timedelta(hours=6):
        return "1 minute"
    if span <= timedelta(days=1):
        return "5 minutes"
    if span <= timedelta(days=7):
        return "1 hour"
    return "1 day"


async def overview(session: AsyncSession, principal: Principal, mode: str, tenant_id: UUID | None) -> dict:
    tenant_ids = None if principal.role == "super_admin" and tenant_id is None else ({tenant_id} if tenant_id else principal.tenant_ids)
    if principal.role != "super_admin" and tenant_id and tenant_id not in principal.tenant_ids:
        tenant_ids = set()
    host_stmt = select(Host.health_status, func.count()).group_by(Host.health_status)
    tenant_stmt = select(func.count()).select_from(Tenant)
    types = _types(mode)
    if types:
        host_stmt = host_stmt.where(Host.source_type.in_(types))
        tenant_stmt = tenant_stmt.where(Tenant.source_type.in_(types))
    if tenant_ids is not None:
        host_stmt = host_stmt.where(Host.tenant_id.in_(tenant_ids)) if tenant_ids else host_stmt.where(text("false"))
        tenant_stmt = tenant_stmt.where(Tenant.id.in_(tenant_ids)) if tenant_ids else tenant_stmt.where(text("false"))
    counts = {status: count for status, count in (await session.execute(host_stmt)).all()}
    monitored = sum(counts.values())
    incident_stmt = select(func.count()).select_from(Incident).where(Incident.status.notin_(["resolved", "closed"]))
    alert_stmt = select(func.count()).select_from(AlertEvent).where(AlertEvent.state.in_(["firing", "acknowledged", "pending"]))
    if tenant_ids is not None:
        incident_stmt = incident_stmt.where(Incident.tenant_id.in_(tenant_ids)) if tenant_ids else incident_stmt.where(text("false"))
        alert_stmt = alert_stmt.where(AlertEvent.tenant_id.in_(tenant_ids)) if tenant_ids else alert_stmt.where(text("false"))
    now = datetime.now(timezone.utc)
    return {
        "total_tenants": await session.scalar(tenant_stmt) or 0,
        "monitored_hosts": monitored,
        "healthy_hosts": counts.get("healthy", 0),
        "warning_hosts": counts.get("warning", 0),
        "critical_hosts": counts.get("critical", 0),
        "unknown_hosts": counts.get("unknown", 0),
        "active_incidents": await session.scalar(incident_stmt) or 0,
        "active_alerts": await session.scalar(alert_stmt) or 0,
        "no_connected_sources": mode == "real" and monitored == 0,
        "mode": mode,
        "computed_at": now.isoformat(),
        "sources": {"hosts": "postgresql", "alerts": "postgresql", "incidents": "postgresql"},
    }


async def metric_query(
    session: AsyncSession,
    *,
    metric: str,
    start: datetime,
    end: datetime,
    tenant_ids: set[UUID] | None,
    host_id: UUID | None,
    source_types: list[str] | None,
    limit_series: int = 8,
) -> dict:
    bucket = bucket_for(start, end)
    if bucket not in {"1 minute", "5 minutes", "1 hour", "1 day"}:
        raise ValueError("Unsupported bucket")
    clauses = ["metric_name = :metric", "timestamp >= :start", "timestamp < :end"]
    params: dict = {"metric": metric, "start": start, "end": end, "limit_series": limit_series}
    if tenant_ids is not None:
        if not tenant_ids:
            return {"metric": metric, "unit": None, "bucket": bucket, "series": [], "source": "timescaledb"}
        keys = []
        for index, tenant in enumerate(tenant_ids):
            key = f"tenant_{index}"
            keys.append(f":{key}")
            params[key] = tenant
        clauses.append(f"tenant_id IN ({', '.join(keys)})")
    if host_id:
        clauses.append("host_id = :host_id")
        params["host_id"] = host_id
    if source_types:
        keys = []
        for index, source_type in enumerate(source_types):
            key = f"source_{index}"
            keys.append(f":{key}")
            params[key] = source_type
        clauses.append(f"source_type IN ({', '.join(keys)})")
    sql = f"""
        SELECT bucket, host_id, avg_value, min_value, max_value FROM (
            SELECT date_bin(INTERVAL '{bucket}', timestamp, TIMESTAMPTZ '2001-01-01') AS bucket,
                   host_id,
                   avg(value) AS avg_value,
                   min(value) AS min_value,
                   max(value) AS max_value
            FROM metric_samples
            WHERE {' AND '.join(clauses)}
            GROUP BY 1, 2
        ) points
        WHERE host_id IN (
            SELECT host_id FROM metric_samples
            WHERE {' AND '.join(clauses)}
            GROUP BY host_id
            ORDER BY avg(value) DESC
            LIMIT :limit_series
        )
        ORDER BY host_id, bucket
    """
    result = await session.execute(text(sql), params)
    grouped: dict[str, list] = {}
    for bucket_at, series_host, avg_value, min_value, max_value in result:
        grouped.setdefault(str(series_host), []).append(
            {"t": bucket_at.isoformat(), "avg": avg_value, "min": min_value, "max": max_value}
        )
    names = {}
    if grouped:
        hosts = (await session.scalars(select(Host).where(Host.id.in_([UUID(item) for item in grouped])))).all()
        names = {str(item.id): item.hostname for item in hosts}
    unit_row = await session.execute(text("SELECT unit FROM metric_samples WHERE metric_name = :metric LIMIT 1"), {"metric": metric})
    unit = unit_row.scalar()
    return {
        "metric": metric,
        "unit": unit,
        "bucket": bucket,
        "series": [{"host_id": key, "hostname": names.get(key, key), "points": points[-500:]} for key, points in grouped.items()],
        "source": "timescaledb",
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def escape_logql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def search_logs(filters: dict, tenant_ids: list[str]) -> dict:
    settings = get_settings()
    start = filters["start"]
    end = filters["end"]
    limit = min(int(filters.get("limit") or 100), 200)
    merged = []
    async with httpx.AsyncClient(timeout=20) as client:
        for tenant_id in tenant_ids[:20]:
            labels = [f'tenant_id="{tenant_id}"']
            for field, key in (("host", "host"), ("service", "service"), ("severity", "severity"), ("source_type", "source_type"), ("environment", "environment"), ("seed_id", "seed_id")):
                if filters.get(key):
                    labels.append(f'{field}="{escape_logql(str(filters[key]))}"')
            query = "{" + ",".join(labels) + "}"
            if filters.get("search"):
                query += f' |= "{escape_logql(filters["search"])}"'
            if filters.get("pipeline"):
                pipeline = str(filters["pipeline"]).strip()
                if pipeline.startswith("{") or "tenant_id" in pipeline:
                    raise ValueError("Advanced mode accepts a pipeline suffix only")
                query += " " + pipeline
            response = await client.get(
                f"{settings.loki_url}/loki/api/v1/query_range",
                params={"query": query, "start": int(start.timestamp() * 1e9), "end": int(end.timestamp() * 1e9), "limit": limit, "direction": "backward"},
                headers={"X-Scope-OrgID": tenant_id},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Loki query failed: {response.status_code} {response.text[:300]}")
            for stream in response.json().get("data", {}).get("result", []):
                for ts, line in stream.get("values", []):
                    merged.append({"timestamp": datetime.fromtimestamp(int(ts) / 1e9, timezone.utc).isoformat(), "labels": stream.get("stream", {}), "line": line, "tenant_id": tenant_id})
    merged.sort(key=lambda item: item["timestamp"], reverse=True)
    return {"items": merged[:limit], "source": "loki", "query_status": "success", "count": min(len(merged), limit)}


async def latest_metrics(session: AsyncSession, metric_names: list[str], tenant_ids: set[UUID] | None, source_types: list[str] | None) -> list[dict]:
    params: dict = {}
    metric_keys = []
    for index, name in enumerate(metric_names):
        key = f"metric_{index}"
        metric_keys.append(f":{key}")
        params[key] = name
    clauses = [f"metric_name IN ({', '.join(metric_keys)})", "timestamp > now() - interval '6 hours'"]
    if tenant_ids is not None:
        if not tenant_ids:
            return []
        keys = []
        for index, tenant in enumerate(tenant_ids):
            key = f"tenant_{index}"
            keys.append(f":{key}")
            params[key] = tenant
        clauses.append(f"tenant_id IN ({', '.join(keys)})")
    if source_types:
        keys = []
        for index, source_type in enumerate(source_types):
            key = f"source_{index}"
            keys.append(f":{key}")
            params[key] = source_type
        clauses.append(f"source_type IN ({', '.join(keys)})")
    sql = f"""
        SELECT DISTINCT ON (host_id, metric_name)
               host_id::text, metric_name, value, unit, timestamp
        FROM metric_samples
        WHERE {' AND '.join(clauses)}
        ORDER BY host_id, metric_name, timestamp DESC
    """
    rows = await session.execute(text(sql), params)
    return [{"host_id": host_id, "metric_name": name, "value": value, "unit": unit, "timestamp": stamp.isoformat()} for host_id, name, value, unit, stamp in rows]


async def tenant_health(session: AsyncSession, tenant_id: UUID) -> dict:
    rows = (await session.execute(select(Host.health_status, func.count()).where(Host.tenant_id == tenant_id).group_by(Host.health_status))).all()
    counts = {status: count for status, count in rows}
    alerts = await session.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.tenant_id == tenant_id, AlertEvent.state.in_(["firing", "pending", "acknowledged"]))) or 0
    incidents = await session.scalar(select(func.count()).select_from(Incident).where(Incident.tenant_id == tenant_id, Incident.severity == "critical", Incident.status.notin_(["resolved", "closed"]))) or 0
    score = health_score(
        healthy=counts.get("healthy", 0),
        warning=counts.get("warning", 0),
        critical=counts.get("critical", 0),
        unknown=counts.get("unknown", 0),
        firing_alerts=alerts,
        open_critical_incidents=incidents,
    )
    return {"score": score, "status": status_from_score(score), "hosts": counts, "firing_alerts": alerts, "open_critical_incidents": incidents, "formula": "100/70/25/40 host weights minus alert and critical incident penalties"}
