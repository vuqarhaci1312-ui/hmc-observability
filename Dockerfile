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
        ca-certificates \
        curl \
        nginx \
        gettext-base \
        adduser \
        libfontconfig1 \
    && curl -fsSL -o /tmp/grafana.deb https://dl.grafana.com/oss/release/grafana_11.4.0_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/grafana.deb \
    && rm -rf /tmp/grafana.deb /var/lib/apt/lists/*

COPY backend /opt/hmc
WORKDIR /opt/hmc
RUN pip install --no-cache-dir .

COPY --from=frontend /src/dist /srv/frontend
COPY infrastructure/render /opt/render
COPY start.sh /app/start.sh
RUN chmod +x /opt/render/start.sh /app/start.sh

EXPOSE 10000
CMD ["/opt/render/start.sh"]
