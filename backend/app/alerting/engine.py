"""Alert evaluation over timestamped samples.

Duration uses sample timestamps. An active scenario speed shortens the
required wall of samples so a 20x lab run can still satisfy a 5 minute rule
without writing future timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class RuleSpec:
    code: str
    rule_type: str
    metric_name: str | None
    comparator: str
    threshold: float | None
    for_seconds: int
    resolve_seconds: int
    hysteresis: float = 0.0
    cooldown_seconds: int = 300
    min_volume: float | None = None
    severity: str = "warning"
    expression: dict | None = None


@dataclass
class Point:
    timestamp: datetime
    value: float
    quality: str = "good"


@dataclass
class EvalState:
    state: str = "ok"
    pending_since: datetime | None = None
    firing_since: datetime | None = None
    last_true: datetime | None = None
    last_resolved_at: datetime | None = None
    last_value: float | None = None


@dataclass
class Evaluation:
    state: str
    value: float | None
    reason: str
    observed_seconds: float = 0.0


def compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "gt":
        return value > threshold
    if comparator == "gte":
        return value >= threshold
    if comparator == "lt":
        return value < threshold
    if comparator == "lte":
        return value <= threshold
    if comparator == "eq":
        return value == threshold
    raise ValueError(comparator)


def _clear_value(rule: RuleSpec, firing: bool) -> float | None:
    if rule.threshold is None:
        return None
    if firing and rule.comparator in {"gt", "gte"}:
        return rule.threshold - rule.hysteresis
    if firing and rule.comparator in {"lt", "lte"}:
        return rule.threshold + rule.hysteresis
    return rule.threshold


def condition_true(rule: RuleSpec, points: list[Point], now: datetime, speed: float) -> tuple[bool, float | None, str]:
    good = [point for point in points if point.quality not in {"missing"} and point.timestamp <= now]
    if rule.rule_type in {"absence", "heartbeat"}:
        timeout = max(5.0, rule.for_seconds / max(speed, 1.0))
        if not good:
            return True, None, "no samples"
        age = (now - good[-1].timestamp).total_seconds()
        return age >= timeout, age, "sample age"
    if not good:
        return False, None, "no samples"
    latest = good[-1].value
    if rule.rule_type == "rate_of_change" and len(good) >= 2:
        previous = good[-2]
        dt = max(1.0, (good[-1].timestamp - previous.timestamp).total_seconds())
        latest = (good[-1].value - previous.value) / dt
    if rule.rule_type == "moving_average":
        window = good[-5:]
        latest = sum(point.value for point in window) / len(window)
    if rule.rule_type == "error_rate":
        if rule.min_volume is not None and latest < 0 and False:
            return False, latest, "volume"
        volume_points = [point for point in good if point.quality == "volume"]
        if rule.min_volume is not None and volume_points and volume_points[-1].value < rule.min_volume:
            return False, latest, "below minimum volume"
    if rule.threshold is None:
        return False, latest, "threshold missing"
    threshold = rule.threshold
    return compare(latest, rule.comparator, threshold), latest, rule.metric_name or rule.rule_type


def _held_seconds(points: list[Point], rule: RuleSpec, now: datetime, speed: float) -> float:
    if rule.rule_type in {"absence", "heartbeat"}:
        good = [point for point in points if point.timestamp <= now]
        if not good:
            return rule.for_seconds
        return max(0.0, (now - good[-1].timestamp).total_seconds())
    ordered = [point for point in points if point.timestamp <= now and point.quality != "missing"]
    if not ordered:
        return 0.0
    streak_start = ordered[-1].timestamp
    for point in reversed(ordered):
        active, _, _ = condition_true(rule, [item for item in ordered if item.timestamp <= point.timestamp], point.timestamp, speed)
        if not active:
            break
        streak_start = point.timestamp
    return max(0.0, (ordered[-1].timestamp - streak_start).total_seconds())


def evaluate_rule(
    rule: RuleSpec,
    points: list[Point],
    state: EvalState,
    now: datetime,
    *,
    speed: float = 1.0,
    suppressed: bool = False,
    volume: float | None = None,
) -> tuple[EvalState, Evaluation]:
    active, value, reason = condition_true(rule, points, now, speed)
    if rule.rule_type == "error_rate" and rule.min_volume is not None and (volume or 0) < rule.min_volume:
        active = False
        reason = "below minimum volume"
    required = max(5.0, rule.for_seconds / max(speed, 1.0)) if speed > 1 else float(rule.for_seconds)
    resolve_after = max(5.0, rule.resolve_seconds / max(speed, 1.0)) if speed > 1 else float(rule.resolve_seconds)
    held = _held_seconds(points, rule, now, speed) if active else 0.0
    next_state = EvalState(
        state=state.state,
        pending_since=state.pending_since,
        firing_since=state.firing_since,
        last_true=state.last_true,
        last_resolved_at=state.last_resolved_at,
        last_value=value,
    )
    if suppressed:
        next_state.state = "suppressed"
        return next_state, Evaluation("suppressed", value, "maintenance or silence", held)

    if state.state == "resolved" and state.last_resolved_at is not None:
        if (now - state.last_resolved_at).total_seconds() < rule.cooldown_seconds / max(speed, 1.0):
            if active:
                next_state.state = "suppressed"
                return next_state, Evaluation("suppressed", value, "cooldown", held)

    if active and held >= required and state.state in {"ok", "pending", "resolved", "suppressed"}:
        next_state.state = "firing"
        next_state.firing_since = state.firing_since or now
        next_state.pending_since = state.pending_since or now
        next_state.last_true = now
        return next_state, Evaluation("firing", value, reason, held)
    if active and state.state in {"ok", "resolved"}:
        next_state.state = "pending"
        next_state.pending_since = now
        next_state.last_true = now
        return next_state, Evaluation("pending", value, reason, held)
    if active and state.state == "pending":
        next_state.last_true = now
        return next_state, Evaluation("pending", value, reason, held)
    if active and state.state in {"firing", "acknowledged"}:
        next_state.last_true = now
        next_state.state = state.state
        return next_state, Evaluation(state.state, value, reason, held)
    if not active and state.state in {"firing", "acknowledged", "pending"}:
        quiet_for = 0.0
        if state.last_true is not None:
            quiet_for = (now - state.last_true).total_seconds()
        if state.state == "pending" or quiet_for >= resolve_after:
            next_state.state = "resolved"
            next_state.last_resolved_at = now
            next_state.pending_since = None
            next_state.firing_since = None
            return next_state, Evaluation("resolved", value, "recovered", quiet_for)
        return next_state, Evaluation(state.state, value, "waiting for hysteresis", quiet_for)
    next_state.state = "ok"
    return next_state, Evaluation("ok", value, reason, held)


DEFAULT_RULES = [
    RuleSpec("CPU_WARNING", "static_threshold", "cpu_usage_percent", "gt", 80, 300, 120, hysteresis=5, severity="warning"),
    RuleSpec("CPU_CRITICAL", "static_threshold", "cpu_usage_percent", "gt", 95, 180, 120, hysteresis=5, severity="critical"),
    RuleSpec("MEMORY_CRITICAL", "static_threshold", "memory_usage_percent", "gt", 92, 300, 180, hysteresis=4, severity="critical"),
    RuleSpec("DISK_CRITICAL", "static_threshold", "disk_usage_percent", "gt", 90, 300, 180, hysteresis=3, severity="critical"),
    RuleSpec("SIP_TRUNK_DOWN", "static_threshold", "sip_registered", "eq", 0, 60, 60, severity="critical"),
    RuleSpec("MAIL_QUEUE_CRITICAL", "static_threshold", "mail_queue_size", "gt", 500, 300, 180, severity="critical"),
    RuleSpec("HTTP_ERROR_SPIKE", "error_rate", "service_error_ratio", "gt", 0.10, 120, 120, min_volume=1, severity="critical"),
    RuleSpec("HOST_NO_DATA", "absence", None, "gt", 1, 120, 60, severity="critical"),
    RuleSpec("PACKET_LOSS_HIGH", "static_threshold", "net_packet_loss_ratio", "gt", 0.05, 120, 120, severity="warning"),
    RuleSpec("PBX_QUALITY_DEGRADED", "static_threshold", "sip_jitter_ms", "gt", 40, 120, 120, severity="warning"),
    RuleSpec("SMTP_DEFERRAL_HIGH", "rate_of_change", "mail_deferred_total", "gt", 0.5, 180, 180, severity="critical"),
    RuleSpec("DB_LATENCY_HIGH", "static_threshold", "db_query_latency_p95_ms", "gt", 400, 180, 180, severity="warning"),
    RuleSpec("DB_CONNECTIONS_HIGH", "static_threshold", "db_connections", "gt", 200, 120, 120, severity="critical"),
    RuleSpec("STORAGE_LATENCY_HIGH", "static_threshold", "storage_latency_ms", "gt", 100, 180, 120, severity="critical"),
    RuleSpec("AUTH_FAILURE_SPIKE", "static_threshold", "auth_failures_per_minute", "gt", 20, 60, 120, severity="warning"),
    RuleSpec("CERT_EXPIRING", "static_threshold", "cert_days_remaining", "lt", 14, 60, 300, severity="warning"),
]
