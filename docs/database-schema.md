# Database schema

Identity: `users`, `roles`, `permissions`, `role_permissions`, `user_tenant_memberships`, `sessions`, `api_tokens`.

Infrastructure: `tenants`, `environments`, `regions`, `datacenters`, `clusters`, `hosts`, `virtual_machines`, `host_interfaces`, `services`, `service_instances`, `service_dependencies`, `infrastructure_relationships`.

Telemetry: `telemetry_sources`, `metric_definitions`, `metric_samples`, `metric_aggregates`, `ingestion_batches`, `ingestion_errors`, `source_health`.

Alerting: `alert_rules`, `alert_rule_versions`, `alert_evaluations`, `alert_events`, `maintenance_windows`, `silences`, `notification_records`.

Incidents: `incidents`, `incident_events`, `incident_assignments`, `incident_comments`, `incident_evidence`, `incident_relationships`, `incident_tickets`.

Simulation: `scenario_definitions`, `scenario_runs`, `scenario_events`, `simulation_profiles`, `simulation_jobs`, `simulation_checkpoints`.

Administration: `audit_events`, `system_settings`, `integration_settings`, `data_retention_policies`.

`metric_samples` is a Timescale hypertable partitioned by `timestamp`. The primary key is `(timestamp, id)`. A unique key on `(tenant_id, host_id, service_id, metric_name, timestamp)` makes replays idempotent. Host-level samples use the nil UUID for `service_id`.

Indexes cover tenant+time, host+time, metric+time, and tenant+host+metric+time.

Continuous aggregates: `metric_samples_1m`, `metric_samples_5m`, `metric_samples_1h`, `metric_samples_1d`. Query code also aggregates with `date_bin` so charts work before a refresh policy has caught up. The chosen bucket grows with the requested range, and each series returned to the browser is capped.

Log bodies are not copied into PostgreSQL. Loki is the log store.

Migration: `backend/alembic/versions/0001_initial.py`.
