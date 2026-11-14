import logging
from time import perf_counter

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

import httpx
from sqlalchemy import select

from app.api.v1.grafana_routes import router as grafana_router
from app.api.v1.router import router
from app.core.config import get_settings
from app.database.session import get_session_factory
from app.models.entities import Tenant

REQUESTS = Counter("hmc_http_requests_total", "HTTP requests", ["handler", "method", "status"])
LATENCY = Histogram("hmc_http_request_duration_seconds", "HTTP latency", ["handler"])
logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}')


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", openapi_url="/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_and_metrics(request: Request, call_next):
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        handler = request.url.path
        REQUESTS.labels(handler, request.method, str(response.status_code)).inc()
        LATENCY.labels(handler).observe(perf_counter() - started)
        return response

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        from app.services.platform_health import dependency_health
        payload = await dependency_health()
        return Response(content=__import__("json").dumps(payload), status_code=200 if payload["checks"].get("database", {}).get("status") == "up" and payload["checks"].get("redis", {}).get("status") == "up" else 503, media_type="application/json")

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(router, prefix="/api/v1")
    app.include_router(grafana_router, prefix="/api/v1")

    @app.api_route("/internal/loki/{path:path}", methods=["GET", "POST"])
    async def loki_proxy(path: str, request: Request):
        settings = get_settings()
        if request.headers.get("x-hmc-internal") != settings.secret_key:
            return Response(status_code=401)
        org = request.headers.get("x-scope-orgid") or "all"
        if org == "all":
            factory = get_session_factory()
            async with factory() as session:
                ids = [str(item) for item in (await session.scalars(select(Tenant.id))).all()]
            org = "|".join(ids) if ids else "hmc-platform"
        target = f"{settings.loki_url}/loki/{path}"
        body = await request.body()
        async with httpx.AsyncClient(timeout=30) as client:
            upstream = await client.request(request.method, target, params=request.query_params, content=body, headers={"X-Scope-OrgID": org})
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))

    return app


app = create_app()
