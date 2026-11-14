from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AlertView:
    id: str
    rule_code: str
    severity: str
    tenant_id: str
    host_id: str
    service_id: str | None
    scenario_run_id: str | None
    physical_host_id: str | None
    title: str
    evidence: dict = field(default_factory=dict)


@dataclass
class IncidentDraft:
    correlation_key: str
    title: str
    severity: str
    tenant_ids: list[str]
    host_ids: list[str]
    service_ids: list[str]
    alert_ids: list[str]
    hypothesis: str
    confirmed: bool = False


_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def correlation_key(alert: AlertView) -> str:
    if alert.scenario_run_id:
        return f"scenario:{alert.scenario_run_id}"
    if alert.physical_host_id:
        return f"physical:{alert.physical_host_id}"
    return f"host:{alert.host_id}:{alert.rule_code}"


def correlate(alerts: list[AlertView]) -> list[IncidentDraft]:
    groups: dict[str, list[AlertView]] = {}
    for alert in alerts:
        groups.setdefault(correlation_key(alert), []).append(alert)
    drafts: list[IncidentDraft] = []
    for key, grouped in groups.items():
        severity = max(grouped, key=lambda item: _SEVERITY_RANK.get(item.severity, 0)).severity
        tenant_ids = sorted({item.tenant_id for item in grouped if item.tenant_id})
        host_ids = sorted({item.host_id for item in grouped})
        service_ids = sorted({item.service_id for item in grouped if item.service_id})
        physical = next((item.physical_host_id for item in grouped if item.physical_host_id), None)
        if physical:
            hypothesis = f"Probable root cause: physical host {physical} is affecting dependent workloads"
        elif grouped[0].scenario_run_id:
            hypothesis = f"Probable root cause: active scenario {grouped[0].scenario_run_id} on the affected services"
        else:
            hypothesis = f"Probable root cause: {grouped[0].rule_code} on host {grouped[0].host_id}"
        title = grouped[0].title if len({item.rule_code for item in grouped}) == 1 else "Correlated infrastructure incident"
        drafts.append(
            IncidentDraft(
                correlation_key=key,
                title=title,
                severity=severity,
                tenant_ids=tenant_ids,
                host_ids=host_ids,
                service_ids=service_ids,
                alert_ids=[item.id for item in grouped],
                hypothesis=hypothesis,
                confirmed=False,
            )
        )
    return drafts
