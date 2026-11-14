from datetime import datetime, timedelta, timezone

from app.alerting.engine import DEFAULT_RULES, EvalState, Point, evaluate_rule
from app.auth.rbac import AuthorizationError, Principal, source_types_for_mode, tenant_filter
from app.core.healthscore import health_score
from app.core.redaction import redact
from app.incidents.correlate import AlertView, correlate
from app.scenarios.catalog import SCENARIOS
from app.scenarios.machine import phase_at, progress_for
from app.simulation.formulas import Effect, HostProfile, compute_metrics
from app.simulation.profiles import estimate_profile
from uuid import uuid4


def _host() -> HostProfile:
    return HostProfile("web-a", "web", 4, 8 * 1024**3, 200 * 1024**3, 0.1, 0.35, 0.4, 0.8, "web")


def test_metrics_are_deterministic():
    host = _host()
    moment = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
    first = compute_metrics(42, host, moment)
    second = compute_metrics(42, host, moment)
    assert first == second


def test_cpu_overload_increases_latency_and_errors():
    host = _host()
    moment = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    healthy = compute_metrics(7, host, moment)
    failed = compute_metrics(7, host, moment, Effect("cpu_overload", 1))
    assert failed["cpu_usage_percent"][0] > 95
    assert failed["service_latency_p95_ms"][0] > healthy["service_latency_p95_ms"][0]
    assert failed["service_error_ratio"][0] > healthy["service_error_ratio"][0]


def test_disk_failure_reduces_available_bytes():
    host = _host()
    moment = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    failed = compute_metrics(7, host, moment, Effect("disk_full", 1))
    assert failed["disk_usage_percent"][0] > 90
    assert failed["disk_available_bytes"][0] < failed["disk_used_bytes"][0]
    assert failed["disk_usage_percent"][1] == "good"


def test_server_down_omits_samples():
    assert compute_metrics(1, _host(), datetime.now(timezone.utc), Effect("server_down", 1, omit=True)) == {}


def test_phases_cover_the_lifecycle():
    assert phase_at(0.0)[0] == "healthy"
    assert phase_at(0.5)[1] > 0.5
    assert phase_at(0.99)[0] == "healthy_again"
    assert progress_for(10, 100, 20) == 1 or progress_for(10, 100, 5) == 0.5


def test_alert_requires_duration_and_then_fires():
    rule = next(item for item in DEFAULT_RULES if item.code == "CPU_CRITICAL")
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)
    points = [Point(start + timedelta(minutes=index), 98) for index in range(6)]
    state = EvalState()
    state, result = evaluate_rule(rule, points[:1], state, points[0].timestamp)
    assert result.state == "pending"
    state, result = evaluate_rule(rule, points, state, points[-1].timestamp)
    assert result.state == "firing"


def test_short_spike_does_not_fire():
    rule = next(item for item in DEFAULT_RULES if item.code == "CPU_CRITICAL")
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)
    points = [Point(start, 99), Point(start + timedelta(seconds=30), 10)]
    state, result = evaluate_rule(rule, points, EvalState(), points[-1].timestamp)
    assert result.state != "firing"


def test_flapping_does_not_stay_firing():
    rule = next(item for item in DEFAULT_RULES if item.code == "HTTP_ERROR_SPIKE")
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)
    points = []
    for index in range(12):
        value = 0.8 if index % 2 == 0 else 0.01
        points.append(Point(start + timedelta(seconds=20 * index), value))
    state = EvalState()
    result_state = "ok"
    for index in range(len(points)):
        state, result = evaluate_rule(rule, points[: index + 1], state, points[index].timestamp, volume=100)
        result_state = result.state
    assert result_state != "firing"


def test_correlation_collapses_host_failure():
    physical = "host-phys"
    alerts = [
        AlertView(str(uuid4()), "HOST_NO_DATA", "critical", f"tenant-{index}", f"vm-{index}", None, None, physical, "Host down")
        for index in range(3)
    ]
    drafts = correlate(alerts)
    assert len(drafts) == 1
    assert drafts[0].confirmed is False
    assert drafts[0].hypothesis.startswith("Probable root cause")
    assert len(drafts[0].tenant_ids) == 3


def test_tenant_isolation():
    tenant_a = uuid4()
    tenant_b = uuid4()
    user = Principal(uuid4(), "a@example.test", "tenant_viewer", {tenant_a})
    assert tenant_filter(user, tenant_a) == {tenant_a}
    try:
        tenant_filter(user, tenant_b)
        assert False
    except AuthorizationError:
        pass
    admin = Principal(uuid4(), "root@example.test", "super_admin", set())
    assert tenant_filter(admin, None) is None


def test_data_mode_and_redaction_and_score():
    assert source_types_for_mode("real") == ["real"]
    assert source_types_for_mode("mixed") is None
    assert "REDACTED" in redact("password=super-secret-value")
    assert health_score(healthy=10, warning=0, critical=0, unknown=0, firing_alerts=0, open_critical_incidents=0) == 100
    assert health_score(healthy=0, warning=0, critical=0, unknown=0, firing_alerts=0, open_critical_incidents=0) is None


def test_profiles_and_catalog():
    assert len(SCENARIOS) == 20
    quick = estimate_profile("quick")
    large = estimate_profile("large")
    assert quick["metrics"] == 300_000
    assert large["metrics"] >= 10_000_000
    assert large["estimated_disk_mb"] > quick["estimated_disk_mb"]


def test_loki_accepts_old_samples():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "infrastructure" / "loki" / "loki-config.yml").read_text(encoding="utf-8")
    assert "reject_old_samples: false" in text
