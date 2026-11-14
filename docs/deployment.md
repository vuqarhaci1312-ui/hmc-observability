# Deployment

```bash
cp .env.production.example .env
# fill secrets from the environment or a secret manager
docker compose up --build -d
```

Set `COOKIE_SECURE=true` behind HTTPS. Terminate TLS at the reverse proxy or a load balancer in front of it. Leave `DEMO_ADMIN_EMAIL` and `DEMO_ADMIN_PASSWORD` empty.

Persistent volumes: `postgres_data`, `redis_data`, `loki_data`, `prometheus_data`, `grafana_data`. Restarting containers keeps database rows and Loki chunks.

The API container applies Alembic migrations when `RUN_MIGRATIONS=1`. Worker and scheduler wait until the API healthcheck passes.

Optional Alloy is behind the Compose profile `agents` and is not started by a normal `docker compose up`.

Retention defaults are 90 days for metrics and the log retention hours from the environment. Policies are stored in `data_retention_policies`. Timescale retention is installed in the initial migration.

Internal services use Compose DNS names (`postgres`, `redis`, `loki`, `prometheus`, `grafana`, `api`). Do not point containers at `localhost` for each other.

This deployment is a single-node stack. Mimir is intentionally absent. Scale the metric store later behind the same query service.
