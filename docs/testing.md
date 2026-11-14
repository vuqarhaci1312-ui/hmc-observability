# Testing

Backend unit tests cover deterministic metrics, causal CPU and disk behavior, the scenario phase map, alert duration, short spikes, flapping, multi-tenant correlation, RBAC tenant checks, redaction, health score, demo profiles, and the Loki historical-sample setting.

```bash
cd backend
pytest -q
```

These tests do not need Postgres. API integration tests that need TimescaleDB run inside Compose after the stack is up:

```bash
docker compose exec api pytest -q
```

Frontend:

```bash
cd frontend
npm run typecheck
npm test
npm run build
npx playwright test
```

Playwright expects the stack at `PLAYWRIGHT_BASE_URL` (default `http://localhost:8080`).

The end-to-end story exercised by the domain tests and the running services is:

1. Seed creates demo tenants and hosts.
2. The scheduler emits healthy metrics and logs.
3. A CPU overload scenario raises CPU, latency, and error logs.
4. The CPU rule reaches firing only after its duration.
5. Correlation opens one incident.
6. Recovery clears the alert and moves the incident through monitoring to resolved.
7. A tenant user cannot request another tenant id.
8. A shared physical host failure groups guest alerts into one probable root cause.
9. Scenario runs and queue messages are stored, so a worker restart continues from Redis and does not insert a second open incident for the same correlation key.

`make smoke` checks live health, login, tenant list, and the overview summary.
