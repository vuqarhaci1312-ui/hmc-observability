from __future__ import annotations

import json
from datetime import datetime, timezone

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select

from app.database.session import get_redis, get_session_factory
from app.ingestion.pipeline import LOG_STREAM, METRIC_STREAM, insert_metrics, normalize_metric, publish_event, push_logs, remember_series
from app.models.entities import Host
from app.simulation.formulas import host_status


async def handle_payload(payload: dict) -> None:
    kind = payload["kind"]
    factory = get_session_factory()
    if kind == "metrics":
        rows = [normalize_metric(item, source_type=item["source_type"], seed_id=item.get("seed_id")) for item in payload["items"]]
        async with factory() as session:
            await insert_metrics(session, rows)
            grouped: dict = {}
            for row in rows:
                grouped.setdefault(row["host_id"], {}).setdefault(row["metric_name"], row["value"])
            for host_id, metrics in grouped.items():
                host = await session.get(Host, host_id)
                if host:
                    host.health_status = host_status(metrics, False)
                    host.last_seen_at = max(row["timestamp"] for row in rows if row["host_id"] == host_id)
            await session.commit()
        await remember_series(rows)
        tenants = sorted({str(row["tenant_id"]) for row in rows})
        await publish_event({"type": "metric_updated", "tenant_ids": tenants, "count": len(rows)})
    elif kind == "logs":
        count = await push_logs(payload["items"])
        await publish_event({"type": "new_log_batch", "count": count})
    elif kind == "events":
        await publish_event({"type": "platform_event", "count": len(payload.get("items") or [])})
    else:
        raise ValueError(f"Unknown payload {kind}")


async def drain_once(consumer: str, count: int = 20) -> int:
    redis = get_redis()
    streams = (METRIC_STREAM, LOG_STREAM, "ingest:events")
    for stream in streams:
        try:
            await redis.xgroup_create(stream, "workers", id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
    processed = 0
    for stream in streams:
        messages = await redis.xreadgroup("workers", consumer, {stream: ">"}, count=count)
        for _name, entries in messages:
            for message_id, fields in entries:
                try:
                    payload = json.loads(fields["data"])
                    await handle_payload(payload)
                    await redis.xack(stream, "workers", message_id)
                except Exception as exc:
                    try:
                        raw = json.loads(fields["data"])
                    except Exception:
                        raw = {"kind": "invalid"}
                    raw["retries"] = int(raw.get("retries") or 0) + 1
                    raw["error"] = str(exc)
                    if raw["retries"] > 5:
                        await redis.xadd("ingest:dlq", {"data": json.dumps(raw)}, maxlen=10000, approximate=True)
                    else:
                        await redis.xadd(stream, {"data": json.dumps(raw)})
                    await redis.xack(stream, "workers", message_id)
                processed += 1
    return processed


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
