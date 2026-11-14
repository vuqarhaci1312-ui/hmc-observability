#!/bin/bash
set -eu

export PORT="${PORT:-10000}"
export POSTGRES_USER="${POSTGRES_USER:-hmc}"
export POSTGRES_DB="${POSTGRES_DB:-hmc}"
export PGDATA=/data/pg
url_pw=$(python -c "import os, urllib.parse; print(urllib.parse.quote(os.environ['POSTGRES_PASSWORD'], safe=''))")
export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${url_pw}@127.0.0.1:5432/${POSTGRES_DB}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export LOKI_URL="${LOKI_URL:-http://127.0.0.1:3100}"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
export GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
export RENDER_EXTERNAL_URL="${RENDER_EXTERNAL_URL:-http://127.0.0.1:${PORT}}"
export CORS_ORIGINS="${CORS_ORIGINS:-${RENDER_EXTERNAL_URL}}"
export COOKIE_SECURE="${COOKIE_SECURE:-true}"
export APP_ENV="${APP_ENV:-demo}"
export RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
export DEMO_PROFILE="${DEMO_PROFILE:-quick}"
export DEMO_RANDOM_SEED="${DEMO_RANDOM_SEED:-42}"

mkdir -p /data/pg /data/redis /data/loki /data/grafana /data/prometheus
ln -sfn /data/loki /loki
if [ -d /var/lib/grafana ] && [ ! -L /var/lib/grafana ]; then
  rm -rf /var/lib/grafana
fi
ln -sfn /data/grafana /var/lib/grafana

if [ ! -s "${PGDATA}/PG_VERSION" ]; then
  install -d -o postgres -g postgres "${PGDATA}"
  su -s /bin/bash postgres -c "/usr/lib/postgresql/16/bin/initdb -D ${PGDATA} --username=postgres --auth-local=peer --auth-host=scram-sha-256"
  cat >> "${PGDATA}/postgresql.conf" <<'EOF'
listen_addresses = '127.0.0.1'
shared_preload_libraries = 'timescaledb'
shared_buffers = 128MB
max_connections = 40
EOF
fi
chown -R postgres:postgres "${PGDATA}"
su -s /bin/bash postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D ${PGDATA} -l ${PGDATA}/server.log -w start"

sql_pw=$(printf "%s" "${POSTGRES_PASSWORD}" | sed "s/'/''/g")
su -s /bin/bash postgres -c "/usr/lib/postgresql/16/bin/psql -d postgres -v ON_ERROR_STOP=1 -f -" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
    CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${sql_pw}';
  ELSE
    ALTER ROLE ${POSTGRES_USER} WITH LOGIN PASSWORD '${sql_pw}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_DB}')\gexec
SQL

install -d -o redis -g redis /data/redis
su -s /bin/bash redis -c "redis-server --bind 127.0.0.1 --port 6379 --dir /data/redis --appendonly yes --protected-mode yes --daemonize no" &

loki -config.file=/opt/render/loki-config.yml &

if [ "${RUN_MIGRATIONS}" = "1" ]; then
  cd /opt/hmc
  alembic upgrade head
  cd /
fi

cd /opt/hmc
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
export GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD}"
export GF_USERS_ALLOW_SIGN_UP=false
export GF_SERVER_ROOT_URL="${RENDER_EXTERNAL_URL}/grafana/"
export GF_SERVER_SERVE_FROM_SUB_PATH=true
export GF_SERVER_HTTP_ADDR=127.0.0.1
export GF_PATHS_DATA=/data/grafana
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
grafana-server --homepath /usr/share/grafana &

prometheus \
  --config.file=/opt/render/prometheus.yml \
  --storage.tsdb.path=/data/prometheus \
  --storage.tsdb.retention.time=7d \
  --web.listen-address=127.0.0.1:9090 \
  --web.enable-lifecycle &

python /opt/render/seed_once.py &

envsubst '${PORT}' < /opt/render/nginx.conf.template > /etc/nginx/nginx.conf
nginx -t

stop_stack() {
  nginx -s quit || true
  su -s /bin/bash postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D ${PGDATA} -m fast stop" || true
  kill $(jobs -p) >/dev/null 2>&1 || true
}
trap stop_stack TERM INT
nginx -g 'daemon off;' &
nginx_pid=$!
wait "${nginx_pid}"
stop_stack
