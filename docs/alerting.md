# Alerting and incidents

Rules in `app/alerting/engine.py` cover static thresholds, rate of change, moving average, error rate, absence, and heartbeat. Compound expressions can be stored on the rule row. Seeded rules include CPU warning and critical, memory, disk, SIP registration, mail queue, HTTP error ratio, host no-data, packet loss, PBX jitter, SMTP deferral rate, database latency and connections, storage latency, authentication failures, and certificate age.

A rule moves `ok → pending → firing`. It resolves only after the clear condition holds for `resolve_seconds` (scaled during a fast scenario). Cooldown blocks an immediate re-fire. Opening a page does not create an alert. Evaluations are written to `alert_evaluations`.

Incidents are not alerts. `correlate()` groups firing alerts by scenario run, or by physical host when several guests fail together, or by host and rule otherwise. The text is always "Probable root cause" until a person confirms it. One open incident is kept per correlation key so a worker restart does not duplicate it.

Incident statuses: open, acknowledged, investigating, mitigating, monitoring, resolved, closed, reopened. Recovery from telemetry moves an incident to monitoring and then resolved. Close stays a human action. Comments, assignment, priority, evidence, and a local ticket row are stored. The external helpdesk adapter returns `not_configured`.
