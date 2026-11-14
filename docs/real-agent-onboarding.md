# Real agent onboarding

Create a tenant-scoped API token in Ayarlar or `POST /api/v1/admin/api-tokens`. The token's tenant is authoritative.

## Linux metrics and logs

`scripts/hmc_agent.py` reads `/proc/stat` and posts `cpu_usage_percent` to `POST /api/v1/ingest/metrics`.

`infrastructure/alloy/config.alloy` forwards `/var/log/*.log` to `POST /api/v1/ingest/loki/push` with `Authorization: Bearer`. Do not set `X-Scope-OrgID` on the agent. Replace `REPLACE_HMC_HOST` and export `HMC_API_TOKEN`.

node_exporter can be scraped by Prometheus for platform hosts. Those scrapes are platform metrics. Customer host metrics that must appear in TimescaleDB should be posted to the ingest API.

## Windows

Install `windows_exporter` on the host. Forward its log files with an agent that POSTs the HMC log schema, or run a scheduled task equivalent of `hmc_agent.py` against the same metrics endpoint. There is no invented Windows vendor API.

## Applications

Send OTLP-shaped JSON by mapping resource attributes into the ingest schema: `tenant_id`, `host_id`, `service`, `severity`, `message`, and `attributes`. The API accepts the documented JSON bodies, not an unverified vendor endpoint.

## PBX and mail

Forward SIP and mail logs to `/api/v1/ingest/logs` or the Loki push adapter. Map hostname to an existing host. The SIP and mail dashboards read the same metric names the simulator produces (`sip_registered`, `mail_queue_size`, and the rest of the catalog).

## Smoke test

```bash
set DEMO_ADMIN_EMAIL=admin@hmc.local
set DEMO_ADMIN_PASSWORD=your-dev-password
set HMC_URL=http://localhost:8080
python scripts/ingest_smoke.py
```

Then open Real mode. The sample is `source_type=real`.
