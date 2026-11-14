"""Deterministic metric formulas.

The same seed, host key, metric and timestamp always return the same gauge.
Scenario effects are applied on top of the baseline so healthy and failing
periods stay causally consistent.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)

COUNTERS = {
    "net_errors_total",
    "net_retransmits_total",
    "sip_4xx_total",
    "sip_5xx_total",
    "sip_failed_calls",
    "mail_deferred_total",
    "mail_rejected_total",
    "mail_auth_failures_total",
    "db_deadlocks_total",
    "db_slow_queries_total",
    "storage_io_errors_total",
}

UNITS = {
    "cpu_usage_percent": "percent",
    "memory_usage_percent": "percent",
    "memory_used_bytes": "bytes",
    "disk_usage_percent": "percent",
    "disk_used_bytes": "bytes",
    "disk_available_bytes": "bytes",
    "inode_usage_percent": "percent",
    "disk_iops_read": "iops",
    "disk_iops_write": "iops",
    "disk_latency_ms": "ms",
    "net_in_mbps": "Mbps",
    "net_out_mbps": "Mbps",
    "net_packet_loss_ratio": "ratio",
    "net_errors_total": "count",
    "net_retransmits_total": "count",
    "net_latency_ms": "ms",
    "load_average_1m": "load",
    "uptime_seconds": "seconds",
    "service_rps": "rps",
    "service_success_ratio": "ratio",
    "service_error_ratio": "ratio",
    "service_latency_p50_ms": "ms",
    "service_latency_p95_ms": "ms",
    "service_latency_p99_ms": "ms",
    "service_availability": "ratio",
    "service_saturation": "ratio",
    "sip_registered": "bool",
    "sip_active_calls": "count",
    "sip_calls_per_minute": "cpm",
    "sip_setup_success_ratio": "ratio",
    "sip_failed_calls": "count",
    "sip_4xx_total": "count",
    "sip_5xx_total": "count",
    "sip_jitter_ms": "ms",
    "sip_packet_loss_ratio": "ratio",
    "mail_smtp_connections": "count",
    "mail_imap_connections": "count",
    "mail_queue_size": "count",
    "mail_oldest_age_seconds": "seconds",
    "mail_deferred_total": "count",
    "mail_rejected_total": "count",
    "mail_delivery_rate": "mps",
    "mail_auth_failures_total": "count",
    "db_connections": "count",
    "db_active_queries": "count",
    "db_deadlocks_total": "count",
    "db_locks": "count",
    "db_query_latency_p95_ms": "ms",
    "db_query_latency_p99_ms": "ms",
    "db_tps": "tps",
    "db_replication_lag_seconds": "seconds",
    "db_slow_queries_total": "count",
    "storage_utilization_ratio": "ratio",
    "storage_read_mbps": "Mbps",
    "storage_write_mbps": "Mbps",
    "storage_latency_ms": "ms",
    "storage_io_errors_total": "count",
    "cert_days_remaining": "days",
    "auth_failures_per_minute": "cpm",
}

HOST_METRICS = [
    "cpu_usage_percent",
    "memory_usage_percent",
    "memory_used_bytes",
    "disk_usage_percent",
    "disk_used_bytes",
    "disk_available_bytes",
    "inode_usage_percent",
    "disk_iops_read",
    "disk_iops_write",
    "disk_latency_ms",
    "net_in_mbps",
    "net_out_mbps",
    "net_packet_loss_ratio",
    "net_latency_ms",
    "load_average_1m",
    "uptime_seconds",
]

ROLE_METRICS = {
    "web": ["service_rps", "service_success_ratio", "service_error_ratio", "service_latency_p50_ms", "service_latency_p95_ms", "service_latency_p99_ms", "service_availability", "service_saturation"],
    "app": ["service_rps", "service_success_ratio", "service_error_ratio", "service_latency_p50_ms", "service_latency_p95_ms", "service_latency_p99_ms", "service_availability", "service_saturation"],
    "db": ["db_connections", "db_active_queries", "db_locks", "db_query_latency_p95_ms", "db_query_latency_p99_ms", "db_tps", "db_replication_lag_seconds", "service_availability"],
    "mail": ["mail_smtp_connections", "mail_imap_connections", "mail_queue_size", "mail_oldest_age_seconds", "mail_delivery_rate", "service_availability"],
    "pbx": ["sip_registered", "sip_active_calls", "sip_calls_per_minute", "sip_setup_success_ratio", "sip_jitter_ms", "sip_packet_loss_ratio", "service_availability"],
    "storage": ["storage_utilization_ratio", "storage_read_mbps", "storage_write_mbps", "storage_latency_ms", "service_availability"],
    "hypervisor": ["service_availability", "storage_latency_ms"],
    "windows": ["service_rps", "service_error_ratio", "service_availability", "service_latency_p95_ms"],
    "network": ["net_packet_loss_ratio", "net_latency_ms", "service_availability"],
}


def stable_int(*parts: object) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _rng(*parts: object) -> random.Random:
    return random.Random(stable_int(*parts))


def local_hour(ts: datetime) -> int:
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return (aware.astimezone(timezone.utc).hour + 4) % 24


def diurnal(ts: datetime, weight: float) -> float:
    hour = local_hour(ts)
    if 10 <= hour <= 17:
        shape = 0.85
    elif 8 <= hour <= 20:
        shape = 0.55
    else:
        shape = 0.22
    weekend = 0.62 if ts.weekday() >= 5 else 1.0
    seasonal = 1.0 + 0.04 * math.sin(ts.timetuple().tm_yday / 365 * math.tau)
    return shape * weekend * weight * seasonal


def _wave(ts: datetime, phase: float, period: float) -> float:
    return math.sin((ts.timestamp() / period) + phase)


@dataclass
class HostProfile:
    key: str
    role: str
    cpu_cores: int
    memory_bytes: int
    disk_bytes: int
    cpu_idle: float
    memory_working_set: float
    disk_used_ratio: float
    business_weight: float
    workload: str


@dataclass
class Effect:
    kind: str
    progress: float
    omit: bool = False
    flap: bool = False
    virtual_seconds: float = 0.0
    speed: float = 1.0
    scenario_code: str = ""
    extra: dict = field(default_factory=dict)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def metrics_for_role(role: str) -> list[str]:
    return HOST_METRICS + ROLE_METRICS.get(role, ROLE_METRICS["app"])


def _noise(seed: int, key: str, metric: str, ts: datetime, scale: float) -> float:
    bucket = int(ts.timestamp()) // 60
    return _rng(seed, key, metric, bucket).uniform(-scale, scale)


def compute_metrics(seed: int, host: HostProfile, ts: datetime, effect: Effect | None = None) -> dict[str, tuple[float, str]]:
    """Return metric -> (value, quality). Empty when the source is silent."""
    if effect and effect.omit:
        return {}
    load = diurnal(ts, host.business_weight)
    phase = (stable_int(seed, host.key) % 1000) / 1000 * math.tau
    correlated = 0.5 + 0.5 * _wave(ts, phase, 3600)
    cpu = clamp((host.cpu_idle * 100) + load * 55 + correlated * 8 + _noise(seed, host.key, "cpu", ts, 2.5), 1, 97)
    memory_ratio = clamp(host.memory_working_set + load * 0.12 + _noise(seed, host.key, "mem", ts, 0.01), 0.08, 0.9)
    disk_ratio = clamp(host.disk_used_ratio + (ts.timestamp() - EPOCH.timestamp()) / (86400 * 900) + _noise(seed, host.key, "disk", ts, 0.002), 0.05, 0.88)
    packet_loss = clamp(0.0004 + _noise(seed, host.key, "loss", ts, 0.0003), 0, 0.02)
    latency = clamp(8 + load * 20 + _noise(seed, host.key, "lat", ts, 1.5), 1, 80)
    rps = max(0.1, (20 + load * 180) * (0.6 + host.business_weight))
    error_ratio = clamp(0.004 + max(0, cpu - 85) / 400 + _noise(seed, host.key, "err", ts, 0.001), 0, 0.2)
    success_ratio = clamp(1 - error_ratio, 0, 1)
    p50 = clamp(12 + load * 25 + max(0, cpu - 70) * 1.4, 1, 2000)
    p95 = p50 * 2.4
    p99 = p50 * 4.1
    queue = max(0.0, 8 + load * 40 + _noise(seed, host.key, "queue", ts, 3))
    calls = max(0.0, load * 80 * host.business_weight)
    progress = effect.progress if effect else 0.0
    kind = effect.kind if effect else ""

    if kind == "cpu_overload":
        cpu = 30 + 68 * progress
        p50 = 20 + 400 * progress
        p95 = p50 * 2.2
        p99 = p50 * 3.5
        error_ratio = clamp(0.01 + 0.25 * progress, 0, 0.8)
        success_ratio = 1 - error_ratio
    elif kind == "memory_leak":
        memory_ratio = clamp(host.memory_working_set + 0.7 * progress, 0.1, 0.99)
        if progress > 0.92:
            error_ratio = 0.35
            success_ratio = 0.65
    elif kind == "disk_full":
        disk_ratio = clamp(host.disk_used_ratio + (0.99 - host.disk_used_ratio) * progress, 0.1, 0.995)
        queue = 20 + 8000 * progress
        error_ratio = clamp(error_ratio + 0.4 * max(0, progress - 0.6), 0, 0.9)
        success_ratio = 1 - error_ratio
    elif kind == "packet_loss":
        packet_loss = clamp(0.002 + 0.12 * progress, 0, 0.4)
        latency = 15 + 350 * progress
        error_ratio = clamp(error_ratio + 0.2 * progress, 0, 0.6)
        success_ratio = 1 - error_ratio
    elif kind == "sip_down":
        calls = calls * (1 - progress)
        success_ratio = clamp(0.99 - 0.95 * progress, 0, 1)
        error_ratio = 1 - success_ratio
    elif kind == "pbx_quality":
        packet_loss = 0.01 + 0.08 * progress
        latency = 20 + 180 * progress
        success_ratio = clamp(0.99 - 0.45 * progress, 0.2, 1)
        error_ratio = 1 - success_ratio
    elif kind == "mail_backlog":
        queue = 30 + 12000 * progress
        success_ratio = clamp(0.99 - 0.5 * progress, 0.2, 1)
    elif kind == "smtp_failure":
        queue = 40 + 4000 * progress
        error_ratio = clamp(0.02 + 0.55 * progress, 0, 0.95)
        success_ratio = 1 - error_ratio
    elif kind == "mail_disk":
        disk_ratio = clamp(0.7 + 0.29 * progress, 0.1, 0.995)
        queue = 50 + 9000 * progress
        error_ratio = clamp(0.02 + 0.6 * max(0, progress - 0.4), 0, 0.95)
        success_ratio = 1 - error_ratio
    elif kind == "db_slow":
        p95 = 20 + 1800 * progress
        p99 = p95 * 1.8
        p50 = 10 + 200 * progress
    elif kind == "db_connections":
        error_ratio = clamp(0.01 + 0.5 * progress, 0, 0.9)
        success_ratio = 1 - error_ratio
    elif kind == "http_5xx":
        error_ratio = clamp(0.01 + 0.45 * progress, 0, 0.9)
        success_ratio = 1 - error_ratio
        rps = rps * (1 + progress)
    elif kind == "storage_latency":
        latency = 10 + 500 * progress
        p95 = 15 + 700 * progress
        error_ratio = clamp(0.005 + 0.25 * progress, 0, 0.7)
        success_ratio = 1 - error_ratio
    elif kind == "multi_service":
        disk_ratio = clamp(0.55 + 0.4 * progress, 0.1, 0.98)
        queue = 40 + 6000 * progress
        p95 = 30 + 900 * progress
        error_ratio = clamp(0.01 + 0.35 * progress, 0, 0.8)
        success_ratio = 1 - error_ratio
        latency = 12 + 250 * progress
    elif kind == "brute_force":
        error_ratio = clamp(error_ratio + 0.05 * progress, 0, 0.3)
    elif kind == "cert_expiry":
        pass
    elif kind == "flap":
        pass

    availability = 0.999 if error_ratio < 0.2 else clamp(1 - error_ratio, 0, 1)
    if effect and effect.flap:
        slot = int(effect.virtual_seconds // 20)
        availability = 1.0 if slot % 2 == 0 else 0.0
        success_ratio = availability
        error_ratio = 1 - success_ratio

    sip_registered = 0.0 if kind == "sip_down" and progress > 0.25 else 1.0
    cert_days = 90 - 80 * progress if kind == "cert_expiry" else 120 + (stable_int(seed, host.key) % 200)

    values: dict[str, float] = {
        "cpu_usage_percent": clamp(cpu, 0, 100),
        "memory_usage_percent": memory_ratio * 100,
        "memory_used_bytes": memory_ratio * host.memory_bytes,
        "disk_usage_percent": disk_ratio * 100,
        "disk_used_bytes": disk_ratio * host.disk_bytes,
        "disk_available_bytes": (1 - disk_ratio) * host.disk_bytes,
        "inode_usage_percent": clamp(disk_ratio * 70 + 5, 1, 99),
        "disk_iops_read": max(0.0, 40 + load * 300 + max(0, cpu - 60)),
        "disk_iops_write": max(0.0, 15 + load * 120),
        "disk_latency_ms": clamp(1.5 + disk_ratio * 8 + (20 * progress if kind in {"disk_full", "mail_disk", "storage_latency"} else 0), 0.2, 500),
        "net_in_mbps": max(0.0, load * 120 * host.business_weight),
        "net_out_mbps": max(0.0, load * 90 * host.business_weight),
        "net_packet_loss_ratio": clamp(packet_loss, 0, 1),
        "net_latency_ms": max(0.2, latency),
        "load_average_1m": clamp((cpu / 100) * host.cpu_cores, 0, host.cpu_cores * 4),
        "uptime_seconds": max(0.0, ts.timestamp() - EPOCH.timestamp()),
        "service_rps": max(0.0, rps),
        "service_success_ratio": clamp(success_ratio, 0, 1),
        "service_error_ratio": clamp(error_ratio, 0, 1),
        "service_latency_p50_ms": max(0.1, p50),
        "service_latency_p95_ms": max(0.1, p95),
        "service_latency_p99_ms": max(0.1, p99),
        "service_availability": clamp(availability, 0, 1),
        "service_saturation": clamp((cpu / 100) * 0.7 + memory_ratio * 0.3, 0, 1),
        "sip_registered": sip_registered,
        "sip_active_calls": max(0.0, calls * (0.15 if kind == "sip_down" else 1)),
        "sip_calls_per_minute": max(0.0, calls / 2),
        "sip_setup_success_ratio": clamp(success_ratio if host.role == "pbx" else 0.99, 0, 1),
        "sip_jitter_ms": clamp(4 + latency * 0.15 + (40 * progress if kind == "pbx_quality" else 0), 0, 400),
        "sip_packet_loss_ratio": clamp(packet_loss + (0.1 * progress if kind == "pbx_quality" else 0), 0, 1),
        "mail_smtp_connections": max(0.0, 5 + load * 40),
        "mail_imap_connections": max(0.0, 4 + load * 30),
        "mail_queue_size": max(0.0, queue if host.role == "mail" or kind in {"mail_backlog", "smtp_failure", "mail_disk", "multi_service", "disk_full"} else 2 + load * 5),
        "mail_oldest_age_seconds": max(0.0, queue * 1.6),
        "mail_delivery_rate": max(0.0, (30 + load * 50) * success_ratio),
        "db_connections": max(1.0, 10 + load * (20 if kind == "db_connections" else 40) + (400 * progress if kind == "db_connections" else 0)),
        "db_active_queries": max(0.0, 2 + load * 15 + (80 * progress if kind == "db_slow" else 0)),
        "db_locks": max(0.0, 1 + (40 * progress if kind == "db_slow" else load)),
        "db_query_latency_p95_ms": max(0.5, p95 if kind in {"db_slow", "multi_service", "storage_latency"} else 8 + load * 20),
        "db_query_latency_p99_ms": max(0.5, p99 if kind in {"db_slow", "multi_service"} else 20 + load * 30),
        "db_tps": max(0.0, 20 + load * 200 * success_ratio),
        "db_replication_lag_seconds": max(0.0, 0.2 + (30 * progress if kind == "db_slow" else load)),
        "storage_utilization_ratio": clamp(disk_ratio, 0, 1),
        "storage_read_mbps": max(0.0, 10 + load * 80),
        "storage_write_mbps": max(0.0, 5 + load * 40),
        "storage_latency_ms": max(0.2, latency if kind in {"storage_latency", "multi_service"} else 2 + disk_ratio * 6),
        "cert_days_remaining": cert_days,
        "auth_failures_per_minute": max(0.0, 0.2 + (80 * progress if kind == "brute_force" else load)),
        "service_availability_flap": availability,
    }
    selected = metrics_for_role(host.role)
    if kind == "cert_expiry":
        selected = selected + ["cert_days_remaining"]
    if kind == "brute_force":
        selected = selected + ["auth_failures_per_minute"]
    output: dict[str, tuple[float, str]] = {}
    for name in selected:
        if name in values:
            output[name] = (float(values[name]), "good")
    return output


def counter_delta(seed: int, host_key: str, metric: str, ts: datetime, effect: Effect | None, dt_seconds: float) -> tuple[float, str]:
    progress = effect.progress if effect else 0.0
    kind = effect.kind if effect else ""
    rates = {
        "net_errors_total": 0.002,
        "net_retransmits_total": 0.01,
        "sip_4xx_total": 0.01,
        "sip_5xx_total": 0.002,
        "sip_failed_calls": 0.005,
        "mail_deferred_total": 0.02,
        "mail_rejected_total": 0.005,
        "mail_auth_failures_total": 0.004,
        "db_deadlocks_total": 0.0005,
        "db_slow_queries_total": 0.01,
        "storage_io_errors_total": 0.0002,
    }
    rate = rates.get(metric, 0.001)
    if kind == "sip_down" and metric in {"sip_4xx_total", "sip_5xx_total", "sip_failed_calls"}:
        rate += 2.5 * progress
    if kind in {"smtp_failure", "mail_backlog", "mail_disk"} and metric in {"mail_deferred_total", "mail_rejected_total"}:
        rate += 1.5 * progress
    if kind == "brute_force" and metric == "mail_auth_failures_total":
        rate += 4 * progress
    if kind == "db_slow" and metric == "db_slow_queries_total":
        rate += 3 * progress
    if kind in {"disk_full", "mail_disk", "storage_latency"} and metric == "storage_io_errors_total":
        rate += 0.4 * progress
    if kind == "packet_loss" and metric in {"net_errors_total", "net_retransmits_total"}:
        rate += 1.2 * progress
    jitter = 1 + _noise(seed, host_key, metric, ts, 0.05)
    quality = "reset" if effect and effect.kind == "collector_restart" and progress < 0.05 else "good"
    increment = 0.0 if quality == "reset" else max(0.0, rate * dt_seconds * jitter)
    return increment, quality


def host_status(metrics: dict[str, float], silent: bool) -> str:
    if silent or not metrics:
        return "unknown"
    cpu = metrics.get("cpu_usage_percent", 0)
    memory = metrics.get("memory_usage_percent", 0)
    disk = metrics.get("disk_usage_percent", 0)
    loss = metrics.get("net_packet_loss_ratio", 0)
    errors = metrics.get("service_error_ratio", 0)
    availability = metrics.get("service_availability", 1)
    if cpu >= 95 or memory >= 92 or disk >= 90 or loss >= 0.05 or errors >= 0.1 or availability <= 0:
        return "critical"
    if cpu >= 80 or memory >= 85 or disk >= 80 or loss >= 0.01 or errors >= 0.03 or availability < 0.98:
        return "warning"
    return "healthy"
