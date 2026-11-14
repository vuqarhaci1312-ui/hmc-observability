# Architecture

HMC Observability Control Center is a single Docker Compose system.

- `reverse-proxy` publishes port 8080 and routes `/` to the React app, `/api` and `/health` to FastAPI, and `/grafana` to Grafana.
- `api` serves the versioned HTTP API, SSE stream, and Prometheus metrics.
- `worker` reads Redis Streams `ingest:metrics` and `ingest:logs`, writes TimescaleDB and Loki, and updates host health.
- `scheduler` advances the demo clock and scenario state machine, enqueues telemetry, and evaluates alert rules.
- `postgres` is TimescaleDB. Relational tables live beside the `metric_samples` hypertable and continuous aggregates.
- `redis` is the durable queue, rate-limit counter, idempotency cache, and live series cache.
- `loki` stores log lines. Labels stay low-cardinality. Trace and request identifiers stay in the JSON body.
- `prometheus` scrapes the platform itself.
- `grafana` is an operator console, not a data source for the React app.

Tenant isolation is enforced in `app/auth/rbac.py` before queries. Loki org id is chosen by the API from the caller's memberships.

Demo and real samples share one metric schema. `source_type` is `demo` or `real`. The UI data mode filters that column in the backend.
