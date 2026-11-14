FROM node:22-bookworm-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
        wget \
        unzip \
        nginx \
        redis-server \
        gettext-base \
        adduser \
        libfontconfig1 \
    && curl -fsSL https://packagecloud.io/install/repositories/timescale/timescaledb/script.deb.sh | bash \
    && apt-get install -y --no-install-recommends timescaledb-2-postgresql-16 postgresql-client-16 \
    && curl -fsSL -o /tmp/grafana.deb https://dl.grafana.com/oss/release/grafana_11.4.0_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/grafana.deb \
    && curl -fsSL -o /tmp/loki.zip https://github.com/grafana/loki/releases/download/v3.3.2/loki-linux-amd64.zip \
    && unzip -o /tmp/loki.zip -d /usr/local/bin \
    && mv /usr/local/bin/loki-linux-amd64 /usr/local/bin/loki \
    && chmod +x /usr/local/bin/loki \
    && curl -fsSL -o /tmp/prom.tar.gz https://github.com/prometheus/prometheus/releases/download/v2.55.1/prometheus-2.55.1.linux-amd64.tar.gz \
    && tar -xzf /tmp/prom.tar.gz -C /tmp \
    && mv /tmp/prometheus-2.55.1.linux-amd64/prometheus /usr/local/bin/prometheus \
    && chmod +x /usr/local/bin/prometheus \
    && rm -rf /tmp/grafana.deb /tmp/loki.zip /tmp/prom.tar.gz /tmp/prometheus-2.55.1.linux-amd64 /var/lib/apt/lists/*

COPY backend /opt/hmc
WORKDIR /opt/hmc
RUN pip install --no-cache-dir .

COPY --from=frontend /src/dist /srv/frontend
COPY infrastructure/render /opt/render
COPY infrastructure/prometheus/alerts.yml /opt/render/alerts.yml
RUN chmod +x /opt/render/start.sh

EXPOSE 10000
CMD ["/opt/render/start.sh"]
