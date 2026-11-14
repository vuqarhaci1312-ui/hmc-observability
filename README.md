# HMC Observability Control Center

Multi-tenant observability platform for HMC: infrastructure inventory, metrics, logs, alert evaluation, incident correlation, and a deterministic demo simulator. The React UI reads only backend APIs. Demo telemetry is generated in Python and stored in TimescaleDB and Grafana Loki.

## Architecture

```text
Demo simulator / future agents
        |
        v
FastAPI ingestion (auth, tenant scope, validation, rate limit, redaction)
        |
        v
Redis Streams
        |
        +--> TimescaleDB metric_samples
        +--> Grafana Loki
        |
        v
Alert evaluator --> Incident correlation --> SSE --> React
```

PostgreSQL holds tenants, hosts, services, users, alerts, incidents, and audit records. Prometheus scrapes the API, worker, and scheduler. Grafana is provisioned for platform metrics. Prometheus remote write is not the telemetry store.

See [docs/architecture.md](docs/architecture.md).

## Prerequisites

- Docker Engine and Docker Compose v2
- Make, optional
- Ports: `8080` for the UI and API, Grafana under `/grafana`

No host-level Python or Node install is required for the container workflow.

## Installation

```bash
cp .env.example .env
# Replace SECRET_KEY, POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD,
# and DEMO_ADMIN_PASSWORD. Keep DEMO_ADMIN_* empty in production.
docker compose up --build -d
docker compose exec api alembic upgrade head
```

Migrations also run when `RUN_MIGRATIONS=1` on the API container.

## Demo data

```bash
make seed-demo
# or
docker compose exec api python -m app.cli seed --profile quick --seed 42
```

Profiles:

| Profile | Tenants | Hosts | Logs | Metric samples | History |
| --- | --- | --- | --- | --- | --- |
| quick | 8 | 50 | 100,000 | 300,000 | 7 days |
| standard | 25 | 250 | 750,000 | 3,000,000 | 14 days |
| large | 60 | 600 | 2,000,000 | 10,000,000 | 30 days |

`PROFILE=standard make seed-demo` selects another profile. Standard and large runs are batched. The API returns RAM and disk estimates from `GET /api/v1/simulation/status`. The same seed and scenario produce the same metric formulas.

Demo admin is created only when `APP_ENV` is not `production` and both `DEMO_ADMIN_EMAIL` and `DEMO_ADMIN_PASSWORD` are set. Production users are created with:

```bash
docker compose exec api python -m app.cli create-user --email operator@example.com --password 'replace-me' --role super_admin
```

## Login

Open `http://localhost:8080`, sign in with the development admin from `.env`, then use the tenant switcher, time range, and Demo / Real / Mixed control. Real mode with no agents shows `No connected sources`.

## Scenario Lab

Open Simulyasiya laboratoriyası, start a scenario such as `sip_trunk_down` or `mail_backlog`. The scheduler writes real samples and logs through Redis. The alert evaluator and incident correlation update the Command Center, tenant health, logs, and incident timeline. Pause, resume, stop, and replay call the API.

## API

OpenAPI: `http://localhost:8080/docs`

Authenticated browser sessions use an httpOnly access cookie and a rotating httpOnly refresh cookie. Agents use `hmc_` API tokens from `POST /api/v1/admin/api-tokens`.

## Loki, Prometheus, Grafana

- Loki is multi-tenant. The API sets `X-Scope-OrgID` from the authorized tenant. Client-supplied org headers are ignored.
- Historical demo timestamps are accepted (`reject_old_samples: false`).
- Prometheus scrapes `/metrics` inside the Docker network. It is not published as a browser data API.
- Grafana: `http://localhost:8080/grafana` with `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`.

## Real host onboarding

See [docs/real-agent-onboarding.md](docs/real-agent-onboarding.md). Supported intake is `POST /api/v1/ingest/logs`, `/metrics`, `/events`, and an authenticated Loki push adapter. Vendor APIs that are not configured stay `not_configured`.

## Security

- Argon2 password hashes, JWT access tokens, refresh rotation, RBAC, tenant checks on the server
- No production passwords are committed
- Sensitive log fields are redacted before storage
- External helpdesk integration reports `not_configured` until credentials exist

## Tests

```bash
cd backend && pytest -q
cd frontend && npm run typecheck && npm test && npm run build
make smoke
```

`make smoke` logs in and reads tenants plus the overview summary.

## Production limitations

- Local tickets and in-app notifications only. No email sender and no live external helpdesk.
- Large and standard profiles are supported by the seeder but should be run where disk and RAM match the estimate.
- Grafana's provisioned Loki datasource is for operator exploration; tenant log search in the product goes through the API.
- Mimir is not deployed. TimescaleDB is the metric store for this version.

## Troubleshooting

- API not ready: `docker compose logs api` and confirm Postgres and Redis are healthy.
- Empty demo UI: run `make seed-demo` and keep the worker and scheduler up.
- Loki rejects old logs: confirm `infrastructure/loki/loki-config.yml` still has `reject_old_samples: false`, then recreate the Loki container.
- Real mode is empty: that is expected until an agent posts `source_type=real`.
