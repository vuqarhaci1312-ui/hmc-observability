#!/bin/bash
set -eu

export PORT="${PORT:-10000}"
export APP_ENV="${APP_ENV:-demo}"
export RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
export DEMO_PROFILE="${DEMO_PROFILE:-free}"
export DEMO_RANDOM_SEED="${DEMO_RANDOM_SEED:-42}"
export COOKIE_SECURE="${COOKIE_SECURE:-true}"
export LOKI_URL="${LOKI_URL:-disabled}"
export GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
export GRAFANA_TIMESCALE="${GRAFANA_TIMESCALE:-false}"
export DB_POOL_SIZE="${DB_POOL_SIZE:-2}"
export DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-0}"
export RENDER_EXTERNAL_URL="${RENDER_EXTERNAL_URL:-http://127.0.0.1:${PORT}}"
export CORS_ORIGINS="${CORS_ORIGINS:-${RENDER_EXTERNAL_URL}}"

if [ -z "${DATABASE_URL:-}" ] || [ -z "${REDIS_URL:-}" ] || [ -z "${SECRET_KEY:-}" ]; then
  echo "DATABASE_URL, REDIS_URL and SECRET_KEY are required" >&2
  exit 1
fi

db_host=$(python -c "import os,urllib.parse; print(urllib.parse.urlparse(os.environ['DATABASE_URL']).hostname or '')")
if [[ "${db_host}" == *.render.com ]]; then
  export GRAFANA_SSLMODE=require
else
  export GRAFANA_SSLMODE=disable
fi

cd /opt/hmc
if [ "${RUN_MIGRATIONS}" = "1" ]; then
  alembic upgrade head
fi

python - <<'PY'
import asyncio

from app.database.session import get_session_factory
from app.simulation.seed import ensure_reference_data

async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await ensure_reference_data(session)

asyncio.run(main())
PY

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' &
python -m app.workers.runner &
python -m app.workers.scheduler &

ready=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "${ready}" != "1" ]; then
  echo "API did not become healthy" >&2
  exit 1
fi

export GF_SECURITY_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
export GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-}"
export GF_USERS_ALLOW_SIGN_UP=false
export GF_SERVER_ROOT_URL="${RENDER_EXTERNAL_URL}/grafana/"
export GF_SERVER_SERVE_FROM_SUB_PATH=true
export GF_SERVER_HTTP_ADDR=127.0.0.1
export GF_PATHS_DATA=/tmp/grafana
export GF_SECURITY_ALLOW_EMBEDDING=true
export GF_SECURITY_COOKIE_SAMESITE=lax
export GF_SECURITY_COOKIE_SECURE=true
export GF_AUTH_PROXY_ENABLED=true
export GF_AUTH_PROXY_HEADER_NAME=X-WEBAUTH-USER
export GF_AUTH_PROXY_HEADER_PROPERTY=email
export GF_AUTH_PROXY_AUTO_SIGN_UP=true
export GF_AUTH_PROXY_WHITELIST=127.0.0.1
export GF_USERS_AUTO_ASSIGN_ORG=true
export GF_USERS_AUTO_ASSIGN_ORG_ROLE=Viewer
export GF_USERS_VIEWERS_CAN_EDIT=false
export GF_EXPLORE_ENABLED=false
export GF_ANALYTICS_REPORTING_ENABLED=false
mkdir -p /tmp/grafana
grafana-server --homepath /usr/share/grafana &

python /opt/render/seed_once.py &

envsubst '${PORT}' < /opt/render/nginx.conf.template > /etc/nginx/nginx.conf
nginx -t
nginx -g 'daemon off;' &
nginx_pid=$!
wait "${nginx_pid}"
