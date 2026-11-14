from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.redaction import redact
from app.simulation.formulas import stable_int

_TEMPLATES = {
    "linux_start": ("INFO", "systemd", "service_started", "systemd: service {service} started on {host}"),
    "linux_stop": ("WARNING", "systemd", "service_stopped", "systemd: service {service} stopped on {host}"),
    "ssh_failed": ("WARNING", "sshd", "authentication_failed", "sshd: authentication failed for user deploy from 198.51.100.{n} on {host}"),
    "oom": ("CRITICAL", "kernel", "oom", "kernel: Out of memory: killed process {service} on {host}"),
    "disk_io": ("ERROR", "kernel", "disk_io_error", "kernel: I/O error on device vda sector {n} host {host}"),
    "filesystem": ("WARNING", "kernel", "filesystem_warning", "kernel: filesystem on {host} is {disk:.0f}% full"),
    "iface_down": ("ERROR", "kernel", "interface_down", "kernel: {host} interface eth0 link down"),
    "daemon_restart": ("NOTICE", "systemd", "daemon_restart", "systemd: restarted {service} on {host}"),
    "win_stopped": ("ERROR", "windows", "service_stopped", "Service Control Manager: {service} service terminated unexpectedly"),
    "win_auth": ("WARNING", "windows", "authentication_failed", "Windows logon failed for CORP\\svc-{n}"),
    "win_resource": ("ERROR", "windows", "resource_exhaustion", "Windows resource exhaustion on {host}"),
    "win_crash": ("CRITICAL", "windows", "application_crash", "Application {service} crashed with code 0x{n}"),
    "nginx_access": ("INFO", "nginx", "http_access", "nginx: {code} {method} /api/v1/orders host={host} rt={latency}"),
    "nginx_timeout": ("ERROR", "nginx", "upstream_timeout", "nginx: upstream timed out while reading response on {host}"),
    "nginx_reset": ("ERROR", "nginx", "connection_reset", "nginx: recv() failed (104: connection reset by peer) on {host}"),
    "pg_slow": ("WARNING", "postgresql", "slow_query", "postgresql: duration {latency} ms statement SELECT * FROM orders WHERE tenant = $1"),
    "pg_conn": ("ERROR", "postgresql", "connection_limit", "postgresql: remaining connection slots are reserved on {host}"),
    "pg_deadlock": ("ERROR", "postgresql", "deadlock", "postgresql: deadlock detected on {host}"),
    "pg_lag": ("WARNING", "postgresql", "replication_delay", "postgresql: replication lag {latency} ms on {host}"),
    "pg_restart": ("NOTICE", "postgresql", "restart", "postgresql: database system was shut down and restarted on {host}"),
    "mail_queued": ("INFO", "postfix", "message_queued", "postfix: queued message {n} for delivery"),
    "mail_deferred": ("WARNING", "postfix", "delivery_deferred", "postfix: delivery deferred: host said 450 mailbox busy"),
    "mail_auth": ("WARNING", "postfix", "smtp_auth_failed", "postfix: SASL authentication failed user=user{n}@example.test"),
    "mail_unavailable": ("ERROR", "dovecot", "mailbox_unavailable", "dovecot: mailbox unavailable for user{n}@example.test"),
    "mail_failed": ("ERROR", "postfix", "delivery_failed", "postfix: delivery failed with status=deferred on {host}"),
    "sip_reg_failed": ("ERROR", "pbx", "sip_registration_failed", "pbx: SIP trunk registration failed for trunk-primary response=403"),
    "sip_503": ("ERROR", "pbx", "sip_503", "pbx: SIP 503 Service Unavailable on trunk-primary"),
    "sip_timeout": ("WARNING", "pbx", "call_setup_timeout", "pbx: call setup timeout call-id={n}"),
    "rtp_loss": ("WARNING", "pbx", "rtp_packet_loss", "pbx: RTP packet loss {loss:.2%} jitter={jitter:.0f}ms"),
    "sip_recovery": ("INFO", "pbx", "trunk_recovery", "pbx: SIP trunk registration restored"),
    "vm_restart": ("WARNING", "cloud", "vm_restart", "hypervisor: virtual machine {host} restarted"),
    "hv_unreachable": ("CRITICAL", "cloud", "hypervisor_unreachable", "hypervisor: {host} is unreachable"),
    "storage_latency": ("WARNING", "storage", "latency_increased", "storage: operation latency increased to {latency} ms"),
    "snapshot_failed": ("ERROR", "cloud", "snapshot_failed", "hypervisor: snapshot failed for {host}"),
    "vnet": ("ERROR", "cloud", "virtual_network", "hypervisor: virtual network interface degraded on {host}"),
    "collector_down": ("CRITICAL", "collector", "pipeline_failure", "collector: telemetry pipeline stopped for {host}"),
    "cert_expiring": ("WARNING", "tls", "certificate_expiring", "tls: certificate on {host} expires in {days:.0f} days"),
    "healthy": ("INFO", "agent", "heartbeat", "agent: heartbeat from {host} status=ok"),
}


def make_log(
    *,
    seed: int,
    timestamp: datetime,
    tenant_id: UUID | None,
    host_id: UUID,
    hostname: str,
    service_id: UUID | None,
    service_name: str,
    source_type: str,
    environment: str,
    template_key: str,
    scenario_run_id: UUID | None = None,
    seed_id: str | None = None,
    metrics: dict | None = None,
) -> dict:
    metrics = metrics or {}
    severity, component, event_type, template = _TEMPLATES[template_key]
    number = stable_int(seed, host_id, timestamp.timestamp(), template_key) % 200
    message = template.format(
        host=hostname,
        service=service_name,
        n=number,
        code=200 if severity == "INFO" else 502,
        method="GET",
        latency=round(metrics.get("service_latency_p95_ms", metrics.get("db_query_latency_p95_ms", 12)), 1),
        disk=metrics.get("disk_usage_percent", 10),
        loss=metrics.get("sip_packet_loss_ratio", metrics.get("net_packet_loss_ratio", 0)),
        jitter=metrics.get("sip_jitter_ms", 4),
        days=metrics.get("cert_days_remaining", 90),
    )
    trace = f"{stable_int(seed, 'trace', host_id, timestamp.isoformat()):032x}"[:32]
    return {
        "timestamp": timestamp.isoformat(),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "host_id": str(host_id),
        "hostname": hostname,
        "service_id": str(service_id) if service_id else None,
        "service": service_name,
        "source_type": source_type,
        "environment": environment,
        "severity": severity,
        "component": component,
        "event_type": event_type,
        "message": redact(message),
        "trace_id": trace,
        "request_id": trace[:16],
        "correlation_id": str(scenario_run_id) if scenario_run_id else trace[16:],
        "scenario_run_id": str(scenario_run_id) if scenario_run_id else None,
        "seed_id": seed_id,
        "attributes": {
            "template": template_key,
            "cpu_usage_percent": metrics.get("cpu_usage_percent"),
            "disk_usage_percent": metrics.get("disk_usage_percent"),
            "mail_queue_size": metrics.get("mail_queue_size"),
        },
    }


def templates_for_state(role: str, metrics: dict, effect_kind: str | None, recovering: bool) -> list[str]:
    chosen: list[str] = []
    if recovering:
        if role == "pbx":
            chosen.append("sip_recovery")
        elif role in {"web", "app", "windows"}:
            chosen.append("daemon_restart")
        else:
            chosen.append("healthy")
        return chosen
    if effect_kind == "pipeline_failure":
        return ["collector_down"]
    if effect_kind == "server_down":
        return ["hv_unreachable"] if role == "hypervisor" else ["linux_stop"]
    if effect_kind == "brute_force":
        return ["ssh_failed", "mail_auth" if role == "mail" else "win_auth"]
    if effect_kind == "cert_expiry":
        return ["cert_expiring"]
    if metrics.get("memory_usage_percent", 0) >= 96:
        chosen.append("oom")
    if metrics.get("disk_usage_percent", 0) >= 90:
        chosen.extend(["filesystem", "disk_io"])
    if metrics.get("cpu_usage_percent", 0) >= 90 and role in {"web", "app"}:
        chosen.extend(["nginx_timeout", "nginx_access"])
    if metrics.get("service_error_ratio", 0) >= 0.1 and role in {"web", "app", "windows"}:
        chosen.append("nginx_reset" if role != "windows" else "win_crash")
    if metrics.get("mail_queue_size", 0) >= 500 and role == "mail":
        chosen.extend(["mail_queued", "mail_deferred", "mail_failed"])
    if metrics.get("sip_registered", 1) == 0 and role == "pbx":
        chosen.extend(["sip_reg_failed", "sip_503", "sip_timeout"])
    if metrics.get("sip_jitter_ms", 0) >= 40 and role == "pbx":
        chosen.append("rtp_loss")
    if metrics.get("db_query_latency_p95_ms", 0) >= 400 and role == "db":
        chosen.extend(["pg_slow", "pg_lag"])
    if metrics.get("db_connections", 0) >= 200 and role == "db":
        chosen.append("pg_conn")
    if metrics.get("storage_latency_ms", 0) >= 100:
        chosen.append("storage_latency")
    if metrics.get("net_packet_loss_ratio", 0) >= 0.05:
        chosen.append("iface_down")
    if not chosen:
        if role == "web":
            chosen.append("nginx_access")
        elif role == "mail":
            chosen.append("mail_queued")
        elif role == "pbx":
            chosen.append("healthy")
        elif role == "db":
            chosen.append("healthy")
        elif role == "windows":
            chosen.append("healthy")
        else:
            chosen.append("healthy")
    return chosen
