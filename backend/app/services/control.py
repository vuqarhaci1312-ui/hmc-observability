from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AlertEvent,
    AlertRule,
    AlertRuleVersion,
    AuditEvent,
    Host,
    Incident,
    IncidentAssignment,
    IncidentComment,
    IncidentEvent,
    ScenarioDefinition,
    ScenarioEvent,
    ScenarioRun,
    Service,
    SimulationJob,
    User,
)
from app.scenarios.catalog import scenario_by_code


async def write_audit(session: AsyncSession, actor: str | None, action: str, target_type: str, target_id: str | None, tenant_id: UUID | None, details: dict) -> None:
    session.add(AuditEvent(actor_email=actor, action=action, target_type=target_type, target_id=target_id, tenant_id=tenant_id, details=details))


async def start_scenario(session: AsyncSession, *, code: str, tenant_id: UUID | None, host_id: UUID | None, speed: float, seed: int) -> ScenarioRun:
    definition = await session.scalar(select(ScenarioDefinition).where(ScenarioDefinition.code == code))
    if definition is None:
        raise ValueError("Scenario not found")
    spec = scenario_by_code(code)
    hosts_stmt = select(Host).where(Host.source_type == "demo")
    if tenant_id:
        hosts_stmt = hosts_stmt.where(Host.tenant_id == tenant_id)
    if host_id:
        hosts_stmt = hosts_stmt.where(Host.id == host_id)
    hosts = list((await session.scalars(hosts_stmt)).all())
    wanted = spec.get("infrastructure_type")
    role_map = {"pbx": "pbx", "mail": "mail", "database": "db", "storage": "storage", "application": "app", "hypervisor": "hypervisor", "host": None, "network": "network", "collector": None, "infrastructure": None}
    role = role_map.get(wanted)
    if role:
        matched = [host for host in hosts if host.role == role]
        hosts = matched or hosts
    if spec.get("multi_tenant"):
        physical = await session.scalar(select(Host).where(Host.role == "hypervisor", Host.physical_host_id.is_(None)))
        if physical:
            children = list((await session.scalars(select(Host).where(Host.physical_host_id == physical.id))).all())
            hosts = [physical, *children]
    if not hosts:
        raise ValueError("No matching host for this scenario")
    chosen = hosts[:8]
    services = list((await session.scalars(select(Service).where(Service.host_id.in_([host.id for host in chosen])))).all())
    run = ScenarioRun(
        definition_id=definition.id,
        status="running",
        phase="healthy",
        speed=speed,
        tenant_id=chosen[0].tenant_id,
        host_ids=[str(host.id) for host in chosen],
        service_ids=[str(service.id) for service in services],
        started_at=datetime.now(timezone.utc),
        seed=seed,
    )
    session.add(run)
    await session.flush()
    session.add(ScenarioEvent(run_id=run.id, phase="healthy", message="Scenario started"))
    await write_audit(session, None, "scenario.start", "scenario_run", str(run.id), run.tenant_id, {"code": code, "speed": speed})
    await session.commit()
    await session.refresh(run)
    return run


async def set_scenario_status(session: AsyncSession, run_id: UUID, status: str) -> ScenarioRun:
    run = await session.get(ScenarioRun, run_id)
    if run is None:
        raise ValueError("Scenario run not found")
    now = datetime.now(timezone.utc)
    counters = dict(run.evaluation or {})
    if status == "paused":
        counters["paused_at"] = now.isoformat()
    if status == "running" and run.status == "paused" and counters.get("paused_at"):
        paused_at = datetime.fromisoformat(counters["paused_at"])
        counters["paused_seconds"] = float(counters.get("paused_seconds") or 0) + (now - paused_at).total_seconds()
        counters.pop("paused_at", None)
    run.evaluation = counters
    run.status = status
    if status in {"stopped", "finished"}:
        run.ended_at = now
    session.add(ScenarioEvent(run_id=run.id, phase=run.phase, message=f"Scenario {status}"))
    await session.commit()
    await session.refresh(run)
    return run


async def acknowledge_alert(session: AsyncSession, alert_id: UUID, actor: str) -> AlertEvent:
    alert = await session.get(AlertEvent, alert_id)
    if alert is None:
        raise ValueError("Alert not found")
    alert.state = "acknowledged"
    alert.acknowledged_at = datetime.now(timezone.utc)
    await write_audit(session, actor, "alert.acknowledge", "alert", str(alert.id), alert.tenant_id, {})
    await session.commit()
    await session.refresh(alert)
    return alert


async def add_rule(session: AsyncSession, payload: dict) -> AlertRule:
    rule = AlertRule(**payload, version=1)
    session.add(rule)
    await session.flush()
    session.add(AlertRuleVersion(rule_id=rule.id, version=1, definition=payload))
    await session.commit()
    await session.refresh(rule)
    return rule


async def patch_rule(session: AsyncSession, rule_id: UUID, changes: dict) -> AlertRule:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise ValueError("Rule not found")
    for key, value in changes.items():
        setattr(rule, key, value)
    rule.version += 1
    session.add(AlertRuleVersion(rule_id=rule.id, version=rule.version, definition=changes))
    await session.commit()
    await session.refresh(rule)
    return rule


async def incident_action(session: AsyncSession, incident_id: UUID, action: str, actor: User | None, payload: dict) -> Incident:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise ValueError("Incident not found")
    now = datetime.now(timezone.utc)
    if action == "acknowledge":
        incident.status = "acknowledged"
        incident.acknowledged_at = now
    elif action == "assign":
        user = await session.get(User, UUID(payload["user_id"]))
        if user is None:
            raise ValueError("User not found")
        incident.assigned_user_id = user.id
        incident.status = "investigating" if incident.status in {"open", "acknowledged"} else incident.status
        session.add(IncidentAssignment(incident_id=incident.id, user_id=user.id, assigned_by=actor.id if actor else None))
    elif action == "priority":
        incident.priority = payload["priority"]
    elif action == "comment":
        session.add(IncidentComment(incident_id=incident.id, author_id=actor.id if actor else None, body=payload["body"]))
    elif action == "resolve":
        incident.status = "resolved"
        incident.resolved_at = now
        incident.remediation = payload.get("remediation") or incident.remediation
    elif action == "reopen":
        incident.status = "reopened"
        incident.resolved_at = None
        incident.closed_at = None
    elif action == "close":
        incident.status = "closed"
        incident.closed_at = now
    else:
        raise ValueError("Unsupported incident action")
    session.add(IncidentEvent(incident_id=incident.id, kind=action, message=payload.get("body") or action, actor=actor.email if actor else "system", details=payload))
    await write_audit(session, actor.email if actor else None, f"incident.{action}", "incident", str(incident.id), incident.tenant_id, payload)
    await session.commit()
    await session.refresh(incident)
    return incident


def detection_summary(run: ScenarioRun, definition: ScenarioDefinition, alerts: list[AlertEvent], incidents: list[Incident]) -> dict:
    expected = set(definition.expected_alerts or [])
    actual = {str((alert.details or {}).get("rule")) for alert in alerts if (alert.details or {}).get("rule")}
    started = run.started_at or datetime.now(timezone.utc)
    firing = [alert.firing_at for alert in alerts if alert.firing_at]
    latency = None
    if firing:
        latency = (min(firing) - started).total_seconds()
    return {
        "expected_alerts": sorted(expected),
        "actual_alerts": sorted(actual),
        "missing_alerts": sorted(expected - actual),
        "unexpected_alerts": sorted(actual - expected),
        "expected_incidents": definition.expected_incidents,
        "actual_incidents": len(incidents),
        "detection_latency_seconds": latency,
        "recovery_time_seconds": (run.ended_at - started).total_seconds() if run.ended_at else None,
        "correct_correlation": len(incidents) <= max(1, definition.expected_incidents),
    }
