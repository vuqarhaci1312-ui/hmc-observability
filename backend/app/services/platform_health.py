from __future__ import annotations

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.database.session import get_engine, get_redis
from app.ingestion.pipeline import LOG_STREAM, METRIC_STREAM


async def dependency_health() -> dict:
    settings = get_settings()
    checks = {}
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = {"status": "up"}
    except Exception as exc:
        checks["database"] = {"status": "down", "error": str(exc)}
    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = {"status": "up", "metric_queue": await redis.xlen(METRIC_STREAM), "log_queue": await redis.xlen(LOG_STREAM)}
    except Exception as exc:
        checks["redis"] = {"status": "down", "error": str(exc)}
    async with httpx.AsyncClient(timeout=3) as client:
        for name, url in {"loki": f"{settings.loki_url}/ready", "prometheus": f"{settings.prometheus_url}/-/ready"}.items():
            try:
                response = await client.get(url)
                checks[name] = {"status": "up" if response.status_code < 400 else "down", "code": response.status_code}
            except Exception as exc:
                checks[name] = {"status": "down", "error": str(exc)}
    overall = "up" if all(item.get("status") == "up" for item in checks.values()) else "degraded"
    return {"status": overall, "checks": checks, "source": "live probes"}
