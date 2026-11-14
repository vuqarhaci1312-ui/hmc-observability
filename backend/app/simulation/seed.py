from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerting.engine import DEFAULT_RULES, EvalState, Point, RuleSpec, evaluate_rule
from app.auth.rbac import ROLE_PERMISSIONS
from app.core.config import get_settings
from app.core.security import hash_password
from app.models.entities import (
    AlertRule,
    AlertRuleVersion,
    AuditEvent,
    Cluster,
    DataRetentionPolicy,
    Datacenter,
    Environment,
    Host,
    HostInterface,
    Incident,
    InfrastructureRelationship,
    IntegrationSetting,
    MetricDefinition,
    MetricSample,
    Permission,
    Region,
    Role,
    RolePermission,
    ScenarioDefinition,
    Service,
    ServiceDependency,
    ServiceInstance,
    SimulationJob,
    SimulationProfile,
    SystemSetting,
    Tenant,
    User,
    UserTenantMembership,
    VirtualMachine,
)
from app.scenarios.catalog import SCENARIOS
from app.scenarios.machine import phase_at
from app.services.alerts import advance_incident_recovery, audit_evaluation, ensure_incident_for_alerts, upsert_alert
from app.simulation.formulas import COUNTERS, UNITS, Effect, HostProfile, compute_metrics, counter_delta, host_status, metrics_for_role
from app.simulation.logs import make_log, templates_for_state
from app.simulation.profiles import PROFILES, estimate_profile
from app.simulation.world import build_world


async def ensure_reference_data(session: AsyncSession) -> None:
    if await session.scalar(select(Role.id).limit(1)):
        return
    roles = {}
    for name, permissions in ROLE_PERMISSIONS.items():
        role = Role(name=name, description=name.replace("_", " "))
        session.add(role)
        await session.flush()
        roles[name] = role
        for permission_name in sorted(permissions):
            permission = await session.scalar(select(Permission).where(Permission.name == permission_name))
            if permission is None:
                permission = Permission(name=permission_name, description=permission_name)
                session.add(permission)
                await session.flush()
            session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    settings = get_settings()
    if settings.demo_admin_email and settings.demo_admin_password and not settings.is_production:
        session.add(
            User(
                email=settings.demo_admin_email,
                full_name="HMC Demo Admin",
                password_hash=hash_password(settings.demo_admin_password),
                role="super_admin",
                is_active=True,
            )
        )
    for name, unit in UNITS.items():
        session.add(
            MetricDefinition(
                name=name,
                unit=unit,
                metric_type="counter" if name in COUNTERS else "gauge",
                description=name.replace("_", " "),
            )
        )
    for rule in DEFAULT_RULES:
        stored = AlertRule(
            code=rule.code,
            name=rule.code.replace("_", " "),
            rule_type=rule.rule_type,
            metric_name=rule.metric_name,
            comparator=rule.comparator,
            threshold=rule.threshold,
            for_seconds=rule.for_seconds,
            resolve_seconds=rule.resolve_seconds,
            hysteresis=rule.hysteresis,
            cooldown_seconds=rule.cooldown_seconds,
            min_volume=rule.min_volume,
            severity=rule.severity,
            enabled=True,
            expression=rule.expression or {},
        )
        session.add(stored)
        await session.flush()
        session.add(AlertRuleVersion(rule_id=stored.id, version=1, definition={"code": rule.code, "threshold": rule.threshold}))
    for scenario in SCENARIOS:
        session.add(
            ScenarioDefinition(
                code=scenario["code"],
                name=scenario["name"],
                description=scenario["description"],
                category=scenario["category"],
                severity=scenario["severity"],
                infrastructure_type=scenario["infrastructure_type"],
                duration_seconds=scenario["duration_seconds"],
                expected_alerts=scenario["expected_alerts"],
                expected_incidents=scenario["expected_incidents"],
                recovery=scenario["recovery"],
                definition=scenario,
            )
        )
    for name in PROFILES:
        estimated = estimate_profile(name)
        session.add(
            SimulationProfile(
                name=name,
                tenant_count=estimated["tenants"],
                host_count=estimated["hosts"],
                log_count=estimated["logs"],
                metric_count=estimated["metrics"],
                history_days=estimated["days"],
                batch_size=estimated["batch_size"],
                estimated_ram_mb=estimated["estimated_ram_mb"],
                estimated_disk_mb=estimated["estimated_disk_mb"],
            )
        )
    session.add(IntegrationSetting(provider="external_helpdesk", status="not_configured", message="No external helpdesk API is configured."))
    session.add(DataRetentionPolicy(data_class="metrics", retain_days=settings.metric_retention_days, enabled=True))
    session.add(DataRetentionPolicy(data_class="logs", retain_days=max(1, settings.log_retention_hours // 24), enabled=True))
    session.add(SystemSetting(key="data_mode", value={"mode": "demo"}))
    session.add(SystemSetting(key="timezone", value={"name": "Asia/Baku"}))
    session.add(AuditEvent(action="bootstrap", target_type="system", details={"env": settings.app_env}))
    await session.commit()


async def _clear_demo(session: AsyncSession) -> None:
    demo_tenants = list((await session.scalars(select(Tenant.id).where(Tenant.source_type == "demo"))).all())
    await session.execute(delete(MetricSample).where(MetricSample.source_type == "demo"))
    await session.execute(delete(Incident).where(Incident.source_type == "demo"))
    if demo_tenants:
        await session.execute(delete(UserTenantMembership).where(UserTenantMembership.tenant_id.in_(demo_tenants)))
    await session.execute(delete(Host).where(Host.source_type == "demo"))
    await session.execute(delete(Tenant).where(Tenant.source_type == "demo"))
    await session.execute(delete(Cluster))
    await session.execute(delete(Datacenter))
    await session.execute(delete(Region))
    await session.commit()


async def seed_demo(profile_name: str, seed: int, *, reseed: bool = False) -> dict:
    from app.database.session import get_session_factory
    from app.ingestion.pipeline import insert_metrics, push_logs

    if profile_name not in PROFILES:
        raise ValueError(f"Unknown profile {profile_name}")
    profile = PROFILES[profile_name]
    estimated = estimate_profile(profile_name)
    factory = get_session_factory()
    async with factory() as session:
        await ensure_reference_data(session)
        if reseed:
            await _clear_demo(session)
        existing = await session.scalar(select(SimulationJob).where(SimulationJob.profile_name == profile_name, SimulationJob.status.in_(["seeding", "running", "paused"])))
        if existing and not reseed:
            return {"status": existing.status, "seed_id": existing.seed_id, "message": "Simulation job already exists"}
        job = SimulationJob(profile_name=profile_name, status="seeding", seed=seed, seed_id=f"{profile_name}-{seed}", message="Building infrastructure", started_at=datetime.now(timezone.utc))
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
        seed_id = job.seed_id

    world = build_world(seed, profile["tenants"], profile["hosts"])
    async with factory() as session:
        session.add_all(Region(id=item["id"], code=item["code"], name=item["name"], timezone=item["timezone"]) for item in world.regions)
        await session.flush()
        session.add_all(Datacenter(id=item["id"], region_id=item["region_id"], name=item["name"], code=item["code"]) for item in world.datacenters)
        await session.flush()
        session.add_all(Cluster(id=item["id"], datacenter_id=item["datacenter_id"], name=item["name"], kind=item["kind"]) for item in world.clusters)
        await session.flush()
        session.add_all(Tenant(**item) for item in world.tenants)
        await session.flush()
        session.add_all(Environment(**item) for item in world.environments)
        await session.flush()
        for host in world.hosts:
            session.add(
                Host(
                    id=host.id,
                    tenant_id=host.tenant_id,
                    cluster_id=host.cluster_id,
                    hostname=host.hostname,
                    host_kind=host.host_kind,
                    role=host.role,
                    os_name=host.os_name,
                    ip_address=host.ip_address,
                    environment=host.environment,
                    cpu_cores=host.cpu_cores,
                    memory_bytes=host.memory_bytes,
                    disk_bytes=host.disk_bytes,
                    source_type="demo",
                    lifecycle_status="active",
                    health_status="unknown",
                    physical_host_id=host.physical_host_id,
                    baseline=host.profile.__dict__,
                    seed_id=seed_id,
                )
            )
        await session.flush()
        session.add_all(HostInterface(**item) for item in world.interfaces)
        session.add_all(VirtualMachine(**item) for item in world.vms)
        session.add_all(Service(**item) for item in world.services)
        await session.flush()
        session.add_all(
            ServiceInstance(service_id=item["id"], host_id=item["host_id"], instance_name=item["name"], status="running")
            for item in world.services
        )
        session.add_all(ServiceDependency(**item) for item in world.dependencies)
        session.add_all(InfrastructureRelationship(**item) for item in world.relationships)
        await session.commit()

    service_by_host: dict = {}
    for service in world.services:
        service_by_host.setdefault(service["host_id"], service)
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=profile["days"])
    series = sum(len(metrics_for_role(host.role)) for host in world.hosts)
    steps = max(1, profile["metrics"] // max(1, series))
    step_seconds = max(60, int((end - start).total_seconds() / steps))
    mail_host = next((host for host in world.hosts if host.role == "mail"), world.hosts[0])
    cpu_host = next((host for host in world.hosts if host.role in {"web", "app"}), world.hosts[0])
    windows = {
        mail_host.id: (end - timedelta(days=min(3, profile["days"] - 1), hours=4), end - timedelta(days=min(3, profile["days"] - 1), hours=2), "mail_backlog"),
        cpu_host.id: (end - timedelta(days=1, hours=3), end - timedelta(days=1, hours=1), "cpu_overload"),
    }
    rules = {}
    async with factory() as session:
        for stored in (await session.scalars(select(AlertRule))).all():
            rules[stored.code] = stored
    counters: dict = {}
    buffer: list[dict] = []
    logs: list[dict] = []
    written_metrics = 0
    written_logs = 0
    states: dict[tuple, EvalState] = {}
    cursor = start
    while cursor <= end and written_metrics < profile["metrics"]:
        for host in world.hosts:
            window = windows.get(host.id)
            if window and window[0] <= cursor <= window[1]:
                continue
            rows, made_logs, _status = _samples_for(seed, host, cursor, None, service_by_host, seed_id, counters, step_seconds, None)
            buffer.extend(rows)
            if written_logs < profile["logs"]:
                logs.extend(made_logs[:1])
        if len(buffer) >= profile["batch_size"]:
            async with factory() as session:
                written_metrics += await insert_metrics(session, buffer[: profile["batch_size"]])
            buffer = buffer[profile["batch_size"] :]
        if len(logs) >= 400:
            written_logs += await push_logs(logs)
            logs = []
        cursor += timedelta(seconds=step_seconds)
    for host_id, (window_start, window_end, kind) in windows.items():
        host = next(item for item in world.hosts if item.id == host_id)
        moment = window_start
        while moment <= window_end:
            progress = (moment - window_start).total_seconds() / max(1, (window_end - window_start).total_seconds())
            _phase, intensity = phase_at(progress)
            effect = Effect(kind=kind, progress=intensity, scenario_code=kind)
            rows, made_logs, flat = _samples_for(seed, host, moment, effect, service_by_host, seed_id, counters, 60, None)
            buffer.extend(rows)
            logs.extend(made_logs)
            await _evaluate_host(factory, rules, host, moment, flat, states, None)
            moment += timedelta(seconds=60)
            if len(buffer) >= profile["batch_size"]:
                async with factory() as session:
                    written_metrics += await insert_metrics(session, buffer)
                buffer = []
            if len(logs) >= 400:
                written_logs += await push_logs(logs)
                logs = []
    if buffer:
        async with factory() as session:
            written_metrics += await insert_metrics(session, buffer)
    if logs and written_logs < profile["logs"]:
        written_logs += await push_logs(logs[: max(0, profile["logs"] - written_logs)])
    async with factory() as session:
        for host in world.hosts:
            metrics = {name: value for name, (value, _quality) in compute_metrics(seed, host.profile, end).items()}
            stored = await session.get(Host, host.id)
            if stored:
                stored.health_status = host_status(metrics, False)
                stored.last_seen_at = end
                stored.uptime_seconds = metrics.get("uptime_seconds", 0)
        job = await session.get(SimulationJob, job_id)
        if job:
            job.status = "running"
            job.progress = 1
            job.metrics_written = written_metrics
            job.logs_written = written_logs
            job.message = "Live generation is ready"
            job.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {
        "status": "running",
        "profile": profile_name,
        "seed": seed,
        "seed_id": seed_id,
        "estimate": estimated,
        "metrics_written": written_metrics,
        "logs_written": written_logs,
        "tenants": len(world.tenants),
        "hosts": len(world.hosts),
    }


async def resume_history(profile_name: str, seed: int) -> dict:
    """Continue an interrupted demo seed from the newest stored sample up to now."""
    from sqlalchemy import text

    from app.database.session import get_session_factory
    from app.ingestion.pipeline import insert_metrics, push_logs

    if profile_name not in PROFILES:
        raise ValueError(f"Unknown profile {profile_name}")
    profile = PROFILES[profile_name]
    factory = get_session_factory()
    async with factory() as session:
        latest = await session.scalar(select(SimulationJob).where(SimulationJob.profile_name == profile_name).order_by(SimulationJob.started_at.desc()))
        stamps = (await session.execute(text("SELECT DISTINCT timestamp FROM metric_samples ORDER BY timestamp DESC LIMIT 2"))).scalars().all()
    if latest is None or not stamps:
        raise RuntimeError("No interrupted seed to resume")
    step_seconds = 1350
    if len(stamps) == 2:
        step_seconds = max(60, int((stamps[0] - stamps[1]).total_seconds()))
    seed_id = latest.seed_id
    job_id = latest.id
    world = build_world(seed, profile["tenants"], profile["hosts"])
    service_by_host: dict = {}
    for service in world.services:
        service_by_host.setdefault(service["host_id"], service)
    cursor = stamps[0] + timedelta(seconds=step_seconds)
    end = datetime.now(timezone.utc).replace(microsecond=0)
    counters: dict = {}
    buffer: list[dict] = []
    logs: list[dict] = []
    written_metrics = 0
    written_logs = 0
    while cursor <= end:
        for host in world.hosts:
            rows, made_logs, _status = _samples_for(seed, host, cursor, None, service_by_host, seed_id, counters, step_seconds, None)
            buffer.extend(rows)
            if written_logs < profile["logs"]:
                logs.extend(made_logs[:1])
        while len(buffer) >= 2000:
            async with factory() as session:
                written_metrics += await insert_metrics(session, buffer[:2000])
            buffer = buffer[2000:]
        if len(logs) >= 400:
            try:
                written_logs += await push_logs(logs)
            except Exception as exc:
                print({"logs_skipped": str(exc)}, flush=True)
            logs = []
        print({"cursor": cursor.isoformat(), "metrics": written_metrics}, flush=True)
        cursor += timedelta(seconds=step_seconds)
    if buffer:
        async with factory() as session:
            written_metrics += await insert_metrics(session, buffer)
    if logs:
        try:
            written_logs += await push_logs(logs)
        except Exception as exc:
            print({"logs_skipped": str(exc)}, flush=True)
    async with factory() as session:
        for host in world.hosts:
            metrics = {name: value for name, (value, _quality) in compute_metrics(seed, host.profile, end).items()}
            stored = await session.get(Host, host.id)
            if stored:
                stored.health_status = host_status(metrics, False)
                stored.last_seen_at = end
                stored.uptime_seconds = metrics.get("uptime_seconds", 0)
        for job in (await session.scalars(select(SimulationJob).where(SimulationJob.status.in_(["seeding", "running", "paused"])))).all():
            if job.id == job_id:
                job.status = "running"
                job.progress = 1
                job.metrics_written = written_metrics
                job.logs_written = written_logs
                job.message = "Live generation is ready"
                job.updated_at = end
            else:
                job.status = "finished"
                job.message = "Superseded by noc seed"
                job.updated_at = end
        await session.commit()
    return {"status": "running", "seed_id": seed_id, "metrics_written": written_metrics, "logs_written": written_logs, "step_seconds": step_seconds}


def _samples_for(seed, host, moment, effect, service_by_host, seed_id, counters, step_seconds, scenario_run_id):
    metrics = compute_metrics(seed, host.profile, moment, effect)
    service = service_by_host.get(host.id)
    tenant_id = host.tenant_id
    rows = []
    flat = {}
    for name, (value, quality) in metrics.items():
        flat[name] = value
        rows.append(
            {
                "timestamp": moment,
                "tenant_id": tenant_id,
                "host_id": host.id,
                "service_id": service["id"] if service else None,
                "metric_name": name,
                "value": value,
                "unit": UNITS.get(name, "value"),
                "source_type": "demo",
                "scenario_run_id": scenario_run_id,
                "labels": {"role": host.role, "hostname": host.hostname},
                "quality": quality,
                "seed_id": seed_id,
            }
        )
    for metric in COUNTERS:
        if host.role == "mail" and not metric.startswith("mail_") and metric not in {"net_errors_total", "net_retransmits_total"}:
            continue
        increment, quality = counter_delta(seed, host.profile.key, metric, moment, effect, step_seconds)
        current = counters.get((host.id, metric), 0.0)
        if quality == "reset":
            current = 0.0
        else:
            current += increment
        counters[(host.id, metric)] = current
        flat[metric] = current
        rows.append(
            {
                "timestamp": moment,
                "tenant_id": tenant_id,
                "host_id": host.id,
                "service_id": service["id"] if service else None,
                "metric_name": metric,
                "value": current,
                "unit": "count",
                "source_type": "demo",
                "scenario_run_id": scenario_run_id,
                "labels": {"role": host.role},
                "quality": quality,
                "seed_id": seed_id,
            }
        )
    normalized = []
    from app.ingestion.pipeline import normalize_metric

    for row in rows:
        if row["tenant_id"] is None:
            continue
        normalized.append(normalize_metric(row, source_type="demo", seed_id=seed_id))
    logs = []
    if service and tenant_id is not None:
        recovering = effect is not None and effect.progress < 0.05 and effect.kind not in {"", None}
        for key in templates_for_state(host.role, flat, effect.kind if effect else None, recovering and effect.progress == 0):
            logs.append(
                make_log(
                    seed=seed,
                    timestamp=moment,
                    tenant_id=tenant_id,
                    host_id=host.id,
                    hostname=host.hostname,
                    service_id=service["id"],
                    service_name=service["name"],
                    source_type="demo",
                    environment=host.environment,
                    template_key=key,
                    scenario_run_id=scenario_run_id,
                    seed_id=seed_id,
                    metrics=flat,
                )
            )
    return normalized, logs, flat


async def _evaluate_host(factory, rules, host, moment, flat, states, scenario_run_id):
    if host.tenant_id is None:
        return
    interesting = {
        "cpu_usage_percent": "CPU_CRITICAL",
        "memory_usage_percent": "MEMORY_CRITICAL",
        "disk_usage_percent": "DISK_CRITICAL",
        "mail_queue_size": "MAIL_QUEUE_CRITICAL",
        "service_error_ratio": "HTTP_ERROR_SPIKE",
    }
    changed = []
    async with factory() as session:
        for metric, code in interesting.items():
            if metric not in flat or code not in rules:
                continue
            stored = rules[code]
            spec = RuleSpec(stored.code, stored.rule_type, stored.metric_name, stored.comparator, stored.threshold, stored.for_seconds, stored.resolve_seconds, stored.hysteresis, stored.cooldown_seconds, stored.min_volume, stored.severity)
            key = (host.id, code)
            state = states.get(key, EvalState())
            point = Point(moment, flat[metric])
            state, result = evaluate_rule(spec, [point], state, moment)
            # Keep a rolling list by stashing points on the state object dynamically.
            history = getattr(state, "history", [])
            previous_history = getattr(states.get(key, EvalState()), "history", [])
            history = (previous_history + [point])[-30:]
            state, result = evaluate_rule(spec, history, states.get(key, EvalState()), moment)
            state.history = history
            states[key] = state
            if result.state in {"firing", "resolved", "pending"}:
                alert = await upsert_alert(
                    session,
                    rule_id=stored.id,
                    code=code,
                    tenant_id=host.tenant_id,
                    host_id=host.id,
                    service_id=None,
                    severity=stored.severity,
                    state=result.state,
                    value=result.value,
                    scenario_run_id=scenario_run_id,
                    physical_host_id=host.physical_host_id,
                    now=moment,
                )
                await audit_evaluation(session, stored.id, host.id, result.state, result.value, result.reason)
                if alert and result.state == "firing":
                    changed.append(alert)
                if result.state == "resolved":
                    incident = await session.scalar(select(Incident).where(Incident.status.notin_(["resolved", "closed"])).limit(1))
                    if incident:
                        await advance_incident_recovery(session, incident, moment)
        if changed:
            await ensure_incident_for_alerts(session, changed, moment)
        await session.commit()
