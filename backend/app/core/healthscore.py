def health_score(
    *,
    healthy: int,
    warning: int,
    critical: int,
    unknown: int,
    firing_alerts: int,
    open_critical_incidents: int,
) -> float | None:
    """Weighted host status, then penalties for alerts and critical incidents.

    host points: healthy 100, warning 70, critical 25, unknown 40.
    alert penalty: 3 points each, capped at 15.
    critical incident penalty: 12 points each, capped at 30.
    """
    total = healthy + warning + critical + unknown
    if total <= 0:
        return None
    base = (healthy * 100 + warning * 70 + critical * 25 + unknown * 40) / total
    score = base - min(15, firing_alerts * 3) - min(30, open_critical_incidents * 12)
    return round(max(0.0, min(100.0, score)), 1)


def status_from_score(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 85:
        return "healthy"
    if score >= 60:
        return "warning"
    return "critical"
