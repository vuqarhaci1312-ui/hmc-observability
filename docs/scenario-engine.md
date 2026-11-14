# Scenario engine

Scenarios are a state machine:

`healthy → degrading → warning → critical → incident_detected → investigating → recovering → healthy_again`

`phase_at` maps progress to a name and an intensity from 0 to 1. Intensity drives metric formulas. The scheduler writes those values through the normal ingestion queue. The UI does not paint a fake overlay.

Virtual elapsed time is real elapsed time multiplied by the run speed (1, 5, or 20). Alert `for` durations are divided by that speed, with a five-second floor, so a lab run can satisfy a five-minute rule without writing future timestamps. Historical seed data uses the real configured duration because the timestamps already span it.

The catalog has 20 scenarios, including CPU overload, memory leak, disk full, packet loss, server down, SIP trunk down, PBX quality, mail backlog, SMTP failure, mail disk, slow queries, connection exhaustion, HTTP 5xx, multi-tenant host failure, storage latency, brute force, certificate expiry, pipeline silence, flapping, and a correlated multi-service failure.

Flapping toggles availability faster than the alert hold window, so it does not open a new incident on every flap. Cooldown, hysteresis, dedup keys, and maintenance suppression are part of the evaluator.

When a run finishes, `evaluation` stores expected versus actual alerts, incident count, detection latency, and whether correlation stayed within the expected incident count.
