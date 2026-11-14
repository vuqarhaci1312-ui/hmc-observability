from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_name: str = "HMC Observability Control Center"
    secret_key: str = Field(min_length=16)
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:8080,http://localhost:5173"

    database_url: str = "postgresql+asyncpg://hmc:hmc@postgres:5432/hmc"
    redis_url: str = "redis://redis:6379/0"
    loki_url: str = "http://loki:3100"
    prometheus_url: str = "http://prometheus:9090"
    grafana_url: str = "http://grafana:3000"
    grafana_admin_user: str = "admin"
    grafana_admin_password: str = ""

    demo_admin_email: str = ""
    demo_admin_password: str = ""
    demo_random_seed: int = 42
    demo_profile: str = "quick"

    ingest_max_batch: int = 1000
    ingest_max_body_bytes: int = 2_000_000
    rate_limit_per_minute: int = 600
    log_retention_hours: int = 720
    metric_retention_days: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def async_database_url(self) -> str:
        return _normalize_database_url(self.database_url, driver="asyncpg")

    @property
    def sync_database_url(self) -> str:
        return _normalize_database_url(self.database_url, driver="psycopg")

    @property
    def database_requires_ssl(self) -> bool:
        host = urlparse(self.async_database_url).hostname or ""
        return host.endswith(".render.com")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


def _normalize_database_url(url: str, driver: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if "+asyncpg" in url or "+psycopg" in url:
        url = url.replace("+asyncpg", "").replace("+psycopg", "")
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in {"ssl", "sslmode"}]
    host = parsed.hostname or ""
    if host.endswith(".render.com"):
        query.append(("sslmode", "require") if driver == "psycopg" else ("ssl", "require"))
    scheme = f"postgresql+{driver}"
    return urlunparse(parsed._replace(scheme=scheme, query=urlencode(query)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
