# Telemetry pipeline

1. The demo scheduler or a real agent builds a batch.
2. `normalize_metric` checks the metric name, unit, label count, and tenant.
3. The batch is appended to a Redis Stream.
4. The worker inserts metrics with `ON CONFLICT DO NOTHING` and pushes logs to Loki under the tenant org id.
5. Recent points are cached in Redis for the evaluator.
6. A throttled `metric_updated` or `new_log_batch` event is published. Individual samples are not streamed to the browser.
7. Queries read TimescaleDB with time bucketing or Loki through the API.

Ingestion endpoints require a super admin, NOC operator, or an API token bound to the tenant. Payload size, batch length, rate limit, and `Idempotency-Key` are enforced.

Quality flags include `good`, `missing`, `stale`, and `reset`. Counter series are cumulative. A reset flag marks a counter restart.

Out-of-order points are stored. TimescaleDB and Loki are configured to accept them. Failed deliveries retry up to five times and then land on `ingest:dlq` and `ingestion_errors`.
