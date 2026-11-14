from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.alerting.engine import DEFAULT_RULES, EvalState, Point, RuleSpec, evaluate_rule
from app.database.session import get_redis, get_session_factory
from app.ingestion.pipeline import LOG_STREAM, METRIC_STREAM, enqueue, parse_time, publish_event
from app.models.entities import AlertRule, Host, Incident, ScenarioDefinition, ScenarioEvent, ScenarioRun, Service, SimulationJob
from app.scenarios.machine import is_terminal, phase_at, progress_for
from app.services.alerts import advance_incident_recovery, audit_evaluation, ensure_incident_for_alerts, upsert_alert
from app.services.control import detection_summary
from app.simulation.formulas import UNITS, Effect, HostProfile, compute_metrics, host_status
from app.simulation.logs import make_log, templates_for_state
from app.simulation.seed import _samples_for


async def simulation_tick() -> None:
    factory = get_session_factory()
    async with factory() as session:
        job_row = await session.scalar(select(SimulationJob).where(SimulationJob.status == "running").order_by(SimulationJob.started_at.desc()))
        if job_row is None:
            return
        job = {"id": job_row.id, "seed": job_row.seed, "seed_id": job_row.seed_id}
        hosts = []
        for host in (await session.scalars(select(Host).where(Host.source_type == "demo", Host.seed_id == job["seed_id"]))).all():
            hosts.append({"id": host.id, "tenant_id": host.tenant_id, "role": host.role, "hostname": host.hostname, "environment": host.environment, "baseline": dict(host.baseline or {}), "physical_host_id": host.physical_host_id})
        runs = []
        for run in (await session.scalars(select(ScenarioRun).where(ScenarioRun.status == "running"))).all():
            runs.append({"id": run.id, "definition_id": run.definition_id, "started_at": run.started_at, "phase": run.phase, "evaluation": dict(run.evaluation or {}), "host_ids": list(run.host_ids or []), "tenant_id": run.tenant_id, "speed": run.speed})
        service_by_host = {}
        for service in (await session.scalars(select(Service))).all():
            service_by_host.setdefault(service.host_id, {"id": service.id, "name": service.name})
        definitions = {}
        for item in (await session.scalars(select(ScenarioDefinition))).all():
            definitions[item.id] = {"duration_seconds": item.duration_seconds, "code": item.code, "definition": dict(item.definition or {})}
    now = datetime.now(timezone.utc)
    effects: dict[UUID, Effect] = {}
    async with factory() as session:
        for run in runs:
            definition = definitions.get(run["definition_id"])
            if definition is None or run["started_at"] is None:
                continue
            paused = float((run["evaluation"] or {}).get("paused_seconds") or 0)
            elapsed = max(0.0, (now - run["started_at"]).total_seconds() - paused)
            progress = progress_for(elapsed, definition["duration_seconds"], run["speed"])
            phase, intensity = phase_at(progress)
            stored = await session.get(ScenarioRun, run["id"])
            if stored is None:
                continue
            if stored.phase != phase:
                session.add(ScenarioEvent(run_id=stored.id, phase=phase, message=f"Phase {phase}"))
                await publish_event({"type": "scenario_progress", "tenant_ids": [str(stored.tenant_id)] if stored.tenant_id else [], "run_id": str(stored.id), "phase": phase})
            stored.phase = phase
            stored.intensity = intensity
            stored.virtual_elapsed_seconds = elapsed * stored.speed
            spec = definition["definition"] or {}
            omit = bool(spec.get("omit")) and phase not in {"healthy", "healthy_again"}
            effect = Effect(kind=spec.get("effect") or definition["code"], progress=intensity, omit=omit, flap=bool(spec.get("flap")), virtual_seconds=elapsed * stored.speed, speed=stored.speed, scenario_code=definition["code"])
            for host_id in run["host_ids"]:
                effects[UUID(str(host_id))] = effect
            if is_terminal(progress):
                stored.status = "finished"
                stored.ended_at = now
                await publish_event({"type": "scenario_finished", "tenant_ids": [str(stored.tenant_id)] if stored.tenant_id else [], "run_id": str(stored.id)})
        await session.commit()
    rows = []
    logs = []
    counters: dict = {}
    for host in hosts:
        profile = HostProfile(**host["baseline"])
        effect = effects.get(host["id"])
        if effect and effect.omit:
            continue
        scenario_id = None
        for run in runs:
            if str(host["id"]) in [str(item) for item in run["host_ids"]]:
                scenario_id = run["id"]
        normalized, made_logs, _flat = _samples_for(job["seed"], type("H", (), {"id": host["id"], "tenant_id": host["tenant_id"], "role": host["role"], "hostname": host["hostname"], "environment": host["environment"], "profile": profile, "physical_host_id": host["physical_host_id"]})(), now, effect, service_by_host, job["seed_id"], counters, 2, scenario_id)
        rows.extend(normalized)
        if made_logs:
            logs.append(made_logs[0])
    for offset in range(0, len(rows), 500):
        chunk = []
        for row in rows[offset : offset + 500]:
            item = {key: (value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value) for key, value in row.items()}
            chunk.append(item)
        await enqueue(METRIC_STREAM, {"kind": "metrics", "items": chunk, "retries": 0})
    if logs:
        await enqueue(LOG_STREAM, {"kind": "logs", "items": logs, "retries": 0})
    redis = get_redis()
    await redis.set("hmc:ingest_rate", str(len(rows) / 2), ex=30)


async def evaluate_live() -> None:
    factory = get_session_factory()
    redis = get_redis()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        rules = list((await session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)))).all())
        runs = list((await session.scalars(select(ScenarioRun).where(ScenarioRun.status == "running"))).all())
        hosts = list((await session.scalars(select(Host).where(Host.source_type == "demo"))).all())
        speed_by_host = {}
        scenario_by_host = {}
        for run in runs:
            for host_id in run.host_ids or []:
                speed_by_host[host_id] = run.speed
                scenario_by_host[host_id] = run.id
        changed = []
        for host in hosts:
            if host.tenant_id is None:
                continue
            speed = speed_by_host.get(str(host.id), 1)
            for rule in rules:
                if rule.rule_type == "absence":
                    timeout = max(5, rule.for_seconds / max(speed, 1))
                    age = (now - host.last_seen_at).total_seconds() if host.last_seen_at else timeout + 1
                    points = []
                    active_now = age >= timeout
                    state_key = f"eval:{rule.id}:{host.id}"
                    raw_state = await redis.get(state_key)
                    state = EvalState(**json.loads(raw_state)) if raw_state else EvalState()
                    if raw_state:
                        loaded = json.loads(raw_state)
                        state = EvalState(state=loaded.get("state", "ok"), pending_since=parse_time(loaded["pending_since"]) if loaded.get("pending_since") else None, last_true=parse_time(loaded["last_true"]) if loaded.get("last_true") else None, last_resolved_at=parse_time(loaded["last_resolved_at"]) if loaded.get("last_resolved_at") else None)
                    spec = _spec(rule)
                    if active_now:
                        result_state, result = evaluate_rule(spec, [Point(host.last_seen_at or now - __import__("datetime").timedelta(seconds=age), 0, "missing")], state, now, speed=speed)
                    else:
                        result_state, result = evaluate_rule(spec, [Point(now, 1)], state, now, speed=speed)
                    await redis.set(state_key, json.dumps(_dump_state(result_state), default=str), ex=86400)
                    if result.state in {"firing", "resolved", "pending"}:
                        alert = await upsert_alert(session, rule_id=rule.id, code=rule.code, tenant_id=host.tenant_id, host_id=host.id, service_id=None, severity=rule.severity, state=result.state, value=result.value, scenario_run_id=scenario_by_host.get(str(host.id)), physical_host_id=host.physical_host_id, now=now)
                        if alert and result.state == "firing":
                            changed.append(alert)
                    continue
                if not rule.metric_name:
                    continue
                raw_points = await redis.lrange(f"series:{host.id}:{rule.metric_name}", 0, 30)
                if not raw_points:
                    continue
                points = []
                for raw in reversed(raw_points):
                    item = json.loads(raw)
                    points.append(Point(parse_time(item["t"]), float(item["v"]), item.get("q") or "good"))
                state_key = f"eval:{rule.id}:{host.id}"
                raw_state = await redis.get(state_key)
                state = EvalState()
                if raw_state:
                    loaded = json.loads(raw_state)
                    state = EvalState(state=loaded.get("state", "ok"), pending_since=parse_time(loaded["pending_since"]) if loaded.get("pending_since") else None, last_true=parse_time(loaded["last_true"]) if loaded.get("last_true") else None, last_resolved_at=parse_time(loaded["last_resolved_at"]) if loaded.get("last_resolved_at") else None)
                result_state, result = evaluate_rule(_spec(rule), points, state, now, speed=speed, volume=points[-1].value if rule.rule_type == "error_rate" else None)
                await redis.set(state_key, json.dumps(_dump_state(result_state), default=str), ex=86400)
                if result.state in {"pending", "firing", "resolved"}:
                    alert = await upsert_alert(session, rule_id=rule.id, code=rule.code, tenant_id=host.tenant_id, host_id=host.id, service_id=None, severity=rule.severity, state=result.state, value=result.value, scenario_run_id=scenario_by_host.get(str(host.id)), physical_host_id=host.physical_host_id, now=now)
                    await audit_evaluation(session, rule.id, host.id, result.state, result.value, result.reason)
                    if alert and result.state == "firing":
                        changed.append(alert)
                        await publish_event({"type": "alert_fired", "tenant_ids": [str(host.tenant_id)], "alert_id": str(alert.id), "rule": rule.code})
                    if result.state == "resolved" and alert:
                        await publish_event({"type": "alert_resolved", "tenant_ids": [str(host.tenant_id)], "alert_id": str(alert.id)})
        if changed:
            incident = await ensure_incident_for_alerts(session, changed, now)
            if incident:
                await publish_event({"type": "incident_created", "tenant_ids": [str(incident.tenant_id)], "incident_id": str(incident.id)})
        open_incidents = list((await session.scalars(select(Incident).where(Incident.status.notin_(["resolved", "closed"])))).all())
        for incident in open_incidents:
            before = incident.status
            await advance_incident_recovery(session, incident, now)
            if incident.status != before:
                await publish_event({"type": "incident_updated", "tenant_ids": [str(incident.tenant_id)], "incident_id": str(incident.id), "status": incident.status})
        for run in runs:
            definition = await session.get(ScenarioDefinition, run.definition_id)
            stored = await session.get(ScenarioRun, run.id)
            if definition and stored:
                alerts = list((await session.scalars(select(__import__("app.models.entities", fromlist=["AlertEvent"]).AlertEvent).where(__import__("app.models.entities", fromlist=["AlertEvent"]).AlertEvent.scenario_run_id == stored.id))).all())
                incidents = list((await session.scalars(select(Incident).where(Incident.scenario_run_id == stored.id))).all())
                stored.alerts_triggered = len([item for item in alerts if item.state in {"firing", "acknowledged", "resolved"}])
                stored.incidents_created = len(incidents)
                if stored.status == "finished":
                    stored.evaluation = {**(stored.evaluation or {}), **detection_summary(stored, definition, alerts, incidents)}
        await session.commit()


def _spec(rule: AlertRule) -> RuleSpec:
    return RuleSpec(rule.code, rule.rule_type, rule.metric_name, rule.comparator, rule.threshold, rule.for_seconds, rule.resolve_seconds, rule.hysteresis, rule.cooldown_seconds, rule.min_volume, rule.severity, rule.expression or None)


def _dump_state(state: EvalState) -> dict:
    return {"state": state.state, "pending_since": state.pending_since.isoformat() if state.pending_since else None, "last_true": state.last_true.isoformat() if state.last_true else None, "last_resolved_at": state.last_resolved_at.isoformat() if state.last_resolved_at else None}
