from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redaction import redact_mapping
from app.database.session import get_redis
from app.models.entities import NIL_UUID, IngestionBatch, IngestionError, MetricSample
from app.simulation.formulas import UNITS

METRIC_STREAM = "ingest:metrics"
LOG_STREAM = "ingest:logs"
EVENT_STREAM = "hmc:eventlog"
EVENT_CHANNEL = "hmc:events"


def parse_time(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_metric(item: dict, *, source_type: str, seed_id: str | None = None) -> dict:
    name = str(item["metric_name"])
    if name not in UNITS and item.get("unit") is None:
        raise ValueError(f"Unknown metric {name}")
    labels = {str(key)[:32]: str(value)[:64] for key, value in dict(item.get("labels") or {}).items()}
    if len(labels) > 12:
        raise ValueError("Too many labels")
    return {
        "id": uuid4(),
        "timestamp": parse_time(item["timestamp"]),
        "tenant_id": UUID(str(item["tenant_id"])) if item.get("tenant_id") else NIL_UUID,
        "host_id": UUID(str(item["host_id"])),
        "service_id": UUID(str(item["service_id"])) if item.get("service_id") else NIL_UUID,
        "metric_name": name,
        "value": float(item["value"]),
        "unit": item.get("unit") or UNITS.get(name, "value"),
        "source_type": source_type,
        "scenario_run_id": UUID(str(item["scenario_run_id"])) if item.get("scenario_run_id") else None,
        "labels": labels,
        "quality": item.get("quality") or "good",
        "seed_id": seed_id or item.get("seed_id"),
    }


async def insert_metrics(session: AsyncSession, rows: list[dict]) -> int:
    if not rows:
        return 0
    # asyncpg rejects statements with more than 32767 bind parameters.
    # A metric row binds 13 columns, so 2000 rows stay under that limit.
    written = 0
    for offset in range(0, len(rows), 2000):
        chunk = rows[offset : offset + 2000]
        statement = pg_insert(MetricSample).values(chunk)
        statement = statement.on_conflict_do_nothing(constraint="uq_metric_point")
        result = await session.execute(statement)
        written += result.rowcount or len(chunk)
    await session.commit()
    return written


async def enqueue(stream: str, payload: dict) -> str:
    redis = get_redis()
    return await redis.xadd(stream, {"data": json.dumps(payload, default=str)}, maxlen=2_000_000, approximate=True)


async def publish_event(event: dict) -> None:
    redis = get_redis()
    body = json.dumps({"id": str(uuid4()), "at": datetime.now(timezone.utc).isoformat(), **event}, default=str)
    await redis.xadd(EVENT_STREAM, {"data": body}, maxlen=500, approximate=True)
    await redis.publish(EVENT_CHANNEL, body)


async def remember_series(rows: list[dict]) -> None:
    redis = get_redis()
    pipe = redis.pipeline()
    for row in rows:
        key = f"series:{row['host_id']}:{row['metric_name']}"
        pipe.lpush(key, json.dumps({"t": row["timestamp"].isoformat(), "v": row["value"], "q": row["quality"]}, default=str))
        pipe.ltrim(key, 0, 40)
        pipe.expire(key, 7200)
    await pipe.execute()


def _stream_key(log: dict) -> tuple:
    return (
        log.get("tenant_id") or "platform",
        log.get("hostname") or log.get("host_id"),
        log.get("service") or "host",
        log.get("severity") or "INFO",
        log.get("source_type") or "real",
        log.get("environment") or "production",
        log.get("component") or "agent",
        log.get("seed_id") or "",
    )


async def push_logs(logs: list[dict]) -> int:
    if not logs:
        return 0
    settings = get_settings()
    grouped: dict[str, dict[tuple, list]] = {}
    for log in logs:
        tenant = str(log.get("tenant_id") or "platform")
        key = _stream_key(log)
        moment = parse_time(log["timestamp"])
        line = json.dumps(redact_mapping(log), default=str)
        grouped.setdefault(tenant, {}).setdefault(key, []).append((str(int(moment.timestamp() * 1_000_000_000)), line))
    async with httpx.AsyncClient(timeout=30) as client:
        for tenant, streams in grouped.items():
            payload_streams = []
            for key, values in streams.items():
                _tenant, host, service, severity, source_type, environment, component, seed_id = key
                labels = {
                    "tenant_id": tenant,
                    "host": str(host),
                    "service": str(service),
                    "severity": str(severity),
                    "source_type": str(source_type),
                    "environment": str(environment),
                    "component": str(component),
                }
                if seed_id:
                    labels["seed_id"] = str(seed_id)
                payload_streams.append({"stream": labels, "values": values})
            for offset in range(0, len(payload_streams), 50):
                response = await client.post(
                    f"{settings.loki_url}/loki/api/v1/push",
                    json={"streams": payload_streams[offset : offset + 50]},
                    headers={"X-Scope-OrgID": tenant},
                )
                if response.status_code not in {200, 204}:
                    raise RuntimeError(f"Loki push failed: {response.status_code} {response.text[:500]}")
    return len(logs)


async def record_batch(session: AsyncSession, *, kind: str, status: str, count: int, source_type: str, idempotency_key: str | None, detail: dict) -> UUID:
    batch = IngestionBatch(
        kind=kind,
        status=status,
        item_count=count,
        error_count=0 if status == "stored" else count,
        idempotency_key=idempotency_key,
        source_type=source_type,
        finished_at=datetime.now(timezone.utc),
        detail=detail,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return batch.id


async def record_error(session: AsyncSession, message: str, payload: dict, batch_id: UUID | None = None) -> None:
    session.add(IngestionError(batch_id=batch_id, message=message, payload=redact_mapping(payload)))
    await session.commit()
