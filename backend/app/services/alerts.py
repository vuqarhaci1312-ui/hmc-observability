from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.incidents.correlate import AlertView, correlate
from app.models.entities import (
    AlertEvent,
    AlertEvaluation,
    Incident,
    IncidentEvent,
    IncidentEvidence,
    IncidentRelationship,
    IncidentTicket,
    NotificationRecord,
)


async def audit_evaluation(session: AsyncSession, rule_id: UUID, host_id: UUID, state: str, value: float | None, reason: str) -> None:
    session.add(AlertEvaluation(rule_id=rule_id, host_id=host_id, state=state, value=value, reason=reason[:300]))


async def upsert_alert(
    session: AsyncSession,
    *,
    rule_id: UUID,
    code: str,
    tenant_id: UUID,
    host_id: UUID,
    service_id: UUID | None,
    severity: str,
    state: str,
    value: float | None,
    scenario_run_id: UUID | None,
    physical_host_id: UUID | None,
    now: datetime,
) -> AlertEvent | None:
    dedup = f"{rule_id}:{host_id}:{service_id or 'host'}"
    existing = await session.scalar(
        select(AlertEvent)
        .where(AlertEvent.dedup_key == dedup, AlertEvent.state.in_(["pending", "firing", "acknowledged", "suppressed"]))
        .limit(1)
    )
    if state in {"ok", "resolved"}:
        if existing and existing.state in {"firing", "acknowledged", "pending"}:
            existing.state = "resolved"
            existing.resolved_at = now
            existing.last_value = value
            return existing
        return None
    if existing:
        previous = existing.state
        existing.state = state if existing.state != "acknowledged" else existing.state
        existing.last_value = value
        if state == "firing" and existing.firing_at is None:
            existing.firing_at = now
        if previous != existing.state:
            return existing
        return None
    if state not in {"pending", "firing"}:
        return None
    correlation = f"scenario:{scenario_run_id}" if scenario_run_id else f"physical:{physical_host_id}" if physical_host_id else f"host:{host_id}:{code}"
    alert = AlertEvent(
        rule_id=rule_id,
        tenant_id=tenant_id,
        host_id=host_id,
        service_id=service_id,
        dedup_key=dedup,
        correlation_key=correlation,
        state=state,
        severity=severity,
        title=f"{code} on host",
        last_value=value,
        scenario_run_id=scenario_run_id,
        pending_since=now,
        started_at=now,
        firing_at=now if state == "firing" else None,
        details={"rule": code, "physical_host_id": str(physical_host_id) if physical_host_id else None},
    )
    session.add(alert)
    await session.flush()
    return alert


async def ensure_incident_for_alerts(session: AsyncSession, alerts: list[AlertEvent], now: datetime) -> Incident | None:
    firing = [alert for alert in alerts if alert.state in {"firing", "acknowledged"}]
    if not firing:
        return None
    views = [
        AlertView(
            id=str(alert.id),
            rule_code=str(alert.details.get("rule") or alert.title),
            severity=alert.severity,
            tenant_id=str(alert.tenant_id),
            host_id=str(alert.host_id),
            service_id=str(alert.service_id) if alert.service_id else None,
            scenario_run_id=str(alert.scenario_run_id) if alert.scenario_run_id else None,
            physical_host_id=(alert.details or {}).get("physical_host_id"),
            title=alert.title,
        )
        for alert in firing
    ]
    drafts = correlate(views)
    incident = None
    for draft in drafts:
        current = await session.scalar(
            select(Incident).where(
                Incident.correlation_key == draft.correlation_key,
                Incident.status.notin_(["resolved", "closed"]),
            )
        )
        if current:
            current.affected_tenant_ids = draft.tenant_ids
            current.affected_host_ids = draft.host_ids
            current.affected_service_ids = draft.service_ids
            incident = current
            continue
        incident = Incident(
            title=draft.title,
            severity=draft.severity,
            priority="p1" if draft.severity == "critical" else "p3",
            status="open",
            tenant_id=UUID(draft.tenant_ids[0]),
            affected_tenant_ids=draft.tenant_ids,
            affected_host_ids=draft.host_ids,
            affected_service_ids=draft.service_ids,
            started_at=now,
            detected_at=now,
            hypothesis=draft.hypothesis,
            cause_confirmed=False,
            correlation_key=draft.correlation_key,
            scenario_run_id=firing[0].scenario_run_id,
            source_type="demo",
        )
        session.add(incident)
        await session.flush()
        session.add(IncidentEvent(incident_id=incident.id, kind="detected", message=draft.hypothesis, actor="correlation-engine"))
        session.add(
            IncidentEvidence(
                incident_id=incident.id,
                kind="alert",
                summary="Related firing alerts",
                payload={"alert_ids": draft.alert_ids, "hypothesis": draft.hypothesis},
            )
        )
        session.add(IncidentTicket(incident_id=incident.id, provider="local", status="open", title=draft.title, body=draft.hypothesis))
        session.add(NotificationRecord(tenant_id=incident.tenant_id, kind="incident", title=draft.title, body=draft.hypothesis, link=f"/incidents/{incident.id}"))
        for alert_id in draft.alert_ids:
            session.add(IncidentRelationship(incident_id=incident.id, related_type="alert", related_id=UUID(alert_id), relationship="triggered_by"))
    return incident


async def advance_incident_recovery(session: AsyncSession, incident: Incident, now: datetime) -> None:
    open_alerts = (
        await session.scalars(
            select(AlertEvent).where(
                AlertEvent.correlation_key == incident.correlation_key,
                AlertEvent.state.in_(["pending", "firing", "acknowledged"]),
            )
        )
    ).all()
    if open_alerts:
        return
    if incident.status in {"open", "acknowledged", "investigating", "mitigating"}:
        incident.status = "monitoring"
        session.add(IncidentEvent(incident_id=incident.id, kind="recovery", message="Telemetry returned below alert thresholds. Monitoring recovery.", actor="correlation-engine"))
        return
    if incident.status == "monitoring":
        incident.status = "resolved"
        incident.resolved_at = now
        session.add(IncidentEvent(incident_id=incident.id, kind="resolved", message="Incident resolved from recovered telemetry.", actor="correlation-engine"))
