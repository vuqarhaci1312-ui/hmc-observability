"""Grafana dashboard documents built from TimescaleDB, Loki, and Prometheus."""

from __future__ import annotations

PG = {"type": "grafana-postgresql-datasource", "uid": "timescale"}
LOKI = {"type": "loki", "uid": "loki"}
PROM = {"type": "prometheus", "uid": "prometheus"}


def _tenant(alias: str, tenant_id: str | None) -> str:
    if tenant_id:
        return f"{alias}.tenant_id = '{tenant_id}'::uuid"
    return f"('${{tenant}}' = 'all' OR {alias}.tenant_id::text = '${{tenant}}')"


def _host(alias: str, host_id: str | None, use_var: bool) -> str:
    if host_id:
        return f"{alias}.id = '{host_id}'::uuid"
    if use_var:
        return f"('${{host}}' = 'all' OR {alias}.id::text = '${{host}}')"
    return "TRUE"


def series_sql(metric: str, tenant_id: str | None = None, host_id: str | None = None, bucket: str = "1m", use_host_var: bool = False) -> str:
    return f"""
SELECT
  $__timeGroupAlias(m."timestamp", '{bucket}'),
  h.hostname AS metric,
  avg(m.value) AS value
FROM metric_samples m
JOIN hosts h ON h.id = m.host_id
WHERE $__timeFilter(m."timestamp")
  AND m.metric_name = '{metric}'
  AND {_tenant('m', tenant_id)}
  AND {_host('h', host_id, use_host_var)}
GROUP BY 1, 2
ORDER BY 1
""".strip()


def stat_sql(expression: str, tenant_id: str | None = None) -> str:
    clause = "TRUE" if tenant_id is None else f"tenant_id = '{tenant_id}'::uuid"
    return f"SELECT {expression} AS value WHERE {clause}" if " WHERE " not in expression.upper() else expression


def _target(sql: str, fmt: str = "time_series") -> dict:
    return {
        "refId": "A",
        "datasource": PG,
        "editorMode": "code",
        "format": fmt,
        "rawQuery": True,
        "rawSql": sql,
    }


def _panel(panel_id: int, title: str, panel_type: str, grid: dict, targets: list, unit: str | None = None, datasource: dict | None = None) -> dict:
    defaults: dict = {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}}
    if unit:
        defaults["unit"] = unit
    return {
        "id": panel_id,
        "type": panel_type,
        "title": title,
        "datasource": datasource or PG,
        "gridPos": grid,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "horizontal",
            "displayMode": "gradient",
            "showUnfilled": True,
            "colorMode": "value",
            "graphMode": "area",
            "textMode": "value",
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
    }


def _timeseries(panel_id: int, title: str, metric: str, grid: dict, tenant_id: str | None, host_id: str | None, unit: str, use_host_var: bool = False) -> dict:
    return _panel(panel_id, title, "timeseries", grid, [_target(series_sql(metric, tenant_id, host_id, use_host_var=use_host_var))], unit)


def _gauge_row(start_id: int, metric: str, y: int, tenant_id: str | None) -> dict:
    return _panel(
        start_id,
        metric,
        "bargauge",
        {"x": 0, "y": y, "w": 24, "h": 6},
        [_target(series_sql(metric, tenant_id, bucket="5m"))],
        "percent",
    )


def templating(include_tenant: bool) -> dict:
    items = []
    if include_tenant:
        items.append(
            {
                "name": "tenant",
                "label": "Tenant",
                "type": "query",
                "datasource": PG,
                "definition": "SELECT id::text AS __value, name AS __text FROM tenants ORDER BY name",
                "query": "SELECT id::text AS __value, name AS __text FROM tenants ORDER BY name",
                "includeAll": True,
                "allValue": "all",
                "refresh": 1,
                "current": {"selected": True, "text": "All", "value": "all"},
            }
        )
    return {"list": items}


def _count_sql(table: str, where: str) -> str:
    return f"SELECT count(*)::float AS value FROM {table} WHERE {where}"


def _agg_series(metric: str, tenant_id: str | None, label: str) -> str:
    return f"""
SELECT
  $__timeGroupAlias(m."timestamp", '1m'),
  '{label}' AS metric,
  avg(m.value) AS value
FROM metric_samples m
WHERE $__timeFilter(m."timestamp")
  AND m.metric_name = '{metric}'
  AND {_tenant('m', tenant_id)}
GROUP BY 1
ORDER BY 1
""".strip()


def _top_series(metric: str, tenant_id: str | None, limit: int = 10) -> str:
    return f"""
SELECT
  $__timeGroupAlias(m."timestamp", '5m'),
  h.hostname AS metric,
  avg(m.value) AS value
FROM metric_samples m
JOIN hosts h ON h.id = m.host_id
WHERE $__timeFilter(m."timestamp")
  AND m.metric_name = '{metric}'
  AND {_tenant('m', tenant_id)}
  AND h.id IN (
    SELECT host_id FROM metric_samples
    WHERE metric_name = '{metric}' AND {_tenant('metric_samples', tenant_id)}
    GROUP BY host_id
    ORDER BY avg(value) DESC
    LIMIT {limit}
  )
GROUP BY 1, 2
ORDER BY 1
""".strip()


def _tenant_table(tenant_id: str | None) -> str:
    tenant_filter = "TRUE" if tenant_id is None else f"t.id = '{tenant_id}'::uuid"
    return f"""
WITH latest AS (
  SELECT DISTINCT ON (host_id, metric_name) host_id, metric_name, value, "timestamp"
  FROM metric_samples
  WHERE metric_name IN (
    'cpu_usage_percent','memory_usage_percent','disk_usage_percent',
    'net_in_mbps','uptime_seconds','service_error_ratio'
  )
  AND "timestamp" > now() - interval '2 minutes'
  ORDER BY host_id, metric_name, "timestamp" DESC
)
SELECT
  t.name AS tenant,
  t.environment,
  region.name AS region,
  t.id::text AS tenant_id,
  count(h.id)::float AS hosts,
  count(h.id) FILTER (WHERE h.health_status = 'healthy')::float AS healthy,
  count(h.id) FILTER (WHERE h.health_status = 'warning')::float AS warning,
  count(h.id) FILTER (WHERE h.health_status = 'critical')::float AS critical,
  CASE
    WHEN bool_or(h.health_status = 'critical') THEN 'critical'
    WHEN bool_or(h.health_status = 'warning') THEN 'warning'
    WHEN bool_or(h.health_status = 'healthy') THEN 'healthy'
    ELSE 'unknown'
  END AS status,
  avg(cpu.value) AS cpu,
  avg(ram.value) AS ram,
  NULL::double precision AS gpu,
  avg(disk.value) AS disk,
  avg(net.value) AS network,
  avg(err.value) AS error_rate,
  avg(up.value) AS uptime,
  coalesce(max(a.alerts), 0)::float AS alerts,
  coalesce(max(i.incidents), 0)::float AS incidents,
  extract(epoch FROM (now() - max(seen."timestamp")))::float AS age_s
FROM tenants t
LEFT JOIN hosts h ON h.tenant_id = t.id
LEFT JOIN LATERAL (
  SELECT r.name
  FROM hosts hx
  JOIN clusters c ON c.id = hx.cluster_id
  JOIN datacenters d ON d.id = c.datacenter_id
  JOIN regions r ON r.id = d.region_id
  WHERE hx.tenant_id = t.id
  GROUP BY r.name
  ORDER BY count(*) DESC
  LIMIT 1
) region ON TRUE
LEFT JOIN latest cpu ON cpu.host_id = h.id AND cpu.metric_name = 'cpu_usage_percent'
LEFT JOIN latest ram ON ram.host_id = h.id AND ram.metric_name = 'memory_usage_percent'
LEFT JOIN latest seen ON seen.host_id = h.id AND seen.metric_name = 'cpu_usage_percent'
LEFT JOIN latest disk ON disk.host_id = h.id AND disk.metric_name = 'disk_usage_percent'
LEFT JOIN latest net ON net.host_id = h.id AND net.metric_name = 'net_in_mbps'
LEFT JOIN latest up ON up.host_id = h.id AND up.metric_name = 'uptime_seconds'
LEFT JOIN latest err ON err.host_id = h.id AND err.metric_name = 'service_error_ratio'
LEFT JOIN (
  SELECT tenant_id, count(*) AS alerts FROM alert_events
  WHERE state IN ('firing','pending','acknowledged') GROUP BY tenant_id
) a ON a.tenant_id = t.id
LEFT JOIN (
  SELECT tenant_id, count(*) AS incidents FROM incidents
  WHERE status NOT IN ('resolved','closed') GROUP BY tenant_id
) i ON i.tenant_id = t.id
WHERE {tenant_filter}
GROUP BY t.id, t.name, t.environment, region.name
ORDER BY CASE
  WHEN bool_or(h.health_status = 'critical') THEN 0
  WHEN bool_or(h.health_status = 'warning') THEN 1
  WHEN bool_or(h.health_status = 'healthy') THEN 2
  ELSE 3
END, t.name
""".strip()


def _problem_series(metric: str, tenant_id: str | None) -> str:
    return f"""
SELECT
  $__timeGroupAlias(m."timestamp", '1m'),
  h.hostname AS metric,
  avg(m.value) AS value
FROM metric_samples m
JOIN hosts h ON h.id = m.host_id
WHERE $__timeFilter(m."timestamp")
  AND m.metric_name = '{metric}'
  AND h.health_status IN ('warning', 'critical')
  AND {_tenant('m', tenant_id)}
GROUP BY 1, 2
ORDER BY 1
""".strip()


def _quiet_table(tenant_id: str | None) -> str:
    tenant_sql = "TRUE" if tenant_id is None else f"h.tenant_id = '{tenant_id}'::uuid"
    return f"""
SELECT
  h.hostname,
  h.health_status AS status,
  cpu.value AS cpu,
  mem.value AS ram,
  disk.value AS disk,
  err.value AS error_ratio,
  h.last_seen_at
FROM hosts h
LEFT JOIN LATERAL (
  SELECT value FROM metric_samples
  WHERE host_id = h.id AND metric_name = 'cpu_usage_percent'
  ORDER BY "timestamp" DESC LIMIT 1
) cpu ON TRUE
LEFT JOIN LATERAL (
  SELECT value FROM metric_samples
  WHERE host_id = h.id AND metric_name = 'memory_usage_percent'
  ORDER BY "timestamp" DESC LIMIT 1
) mem ON TRUE
LEFT JOIN LATERAL (
  SELECT value FROM metric_samples
  WHERE host_id = h.id AND metric_name = 'disk_usage_percent'
  ORDER BY "timestamp" DESC LIMIT 1
) disk ON TRUE
LEFT JOIN LATERAL (
  SELECT value FROM metric_samples
  WHERE host_id = h.id AND metric_name = 'service_error_ratio'
  ORDER BY "timestamp" DESC LIMIT 1
) err ON TRUE
WHERE h.health_status = 'healthy'
  AND {tenant_sql}
ORDER BY h.hostname
""".strip()


def _cell_gauge(field: str, unit: str, maximum: float) -> dict:
    return {
        "matcher": {"id": "byName", "options": field},
        "properties": [
            {"id": "unit", "value": unit},
            {"id": "min", "value": 0},
            {"id": "max", "value": maximum},
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "gradient"}},
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                {"color": "green", "value": None},
                {"color": "orange", "value": maximum * 0.7},
                {"color": "red", "value": maximum * 0.9},
            ]}},
        ],
    }


def _tenant_table_panel(tenant_id: str | None, panel_id: int, grid: dict) -> dict:
    panel = _panel(panel_id, "Tenants", "table", grid, [_target(_tenant_table(tenant_id), "table")])
    panel["fieldConfig"]["defaults"].update({
        "noValue": "N/A",
        "custom": {"align": "auto", "filterable": True, "cellOptions": {"type": "auto"}},
    })
    panel["fieldConfig"]["overrides"] = [
        {"matcher": {"id": "byName", "options": "tenant"}, "properties": [
            {"id": "links", "value": [{"title": "Tenant", "url": "/grafana/d/hmc-tenant?var-tenant=${__data.fields.tenant_id}&kiosk=tv&theme=dark"}]},
        ]},
        {"matcher": {"id": "byName", "options": "tenant_id"}, "properties": [{"id": "custom.hidden", "value": True}]},
        {"matcher": {"id": "byName", "options": "status"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "mappings", "value": [{"type": "value", "options": {
                "healthy": {"text": "healthy", "color": "green", "index": 0},
                "warning": {"text": "warning", "color": "orange", "index": 1},
                "critical": {"text": "critical", "color": "red", "index": 2},
                "unknown": {"text": "unknown", "color": "blue", "index": 3},
            }}]},
        ]},
        _cell_gauge("cpu", "percent", 100),
        _cell_gauge("ram", "percent", 100),
        {"matcher": {"id": "byName", "options": "gpu"}, "properties": [{"id": "noValue", "value": "N/A"}]},
        _cell_gauge("disk", "percent", 100),
        _cell_gauge("network", "Mbits", 200),
        _cell_gauge("error_rate", "percentunit", 1),
        {"matcher": {"id": "byName", "options": "uptime"}, "properties": [{"id": "unit", "value": "s"}]},
        {"matcher": {"id": "byName", "options": "age_s"}, "properties": [{"id": "unit", "value": "s"}, {"id": "displayName", "value": "Age"}]},
    ]
    panel["options"] = {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "alerts", "desc": True}]}
    return panel


def overview(tenant_id: str | None = None) -> dict:
    dashboard = _dashboard("hmc-overview", "Tenant monitoring", [
        _tenant_table_panel(tenant_id, 1, {"x": 0, "y": 0, "w": 24, "h": 28}),
    ], False)
    dashboard["refresh"] = "5s"
    return dashboard


def tenant_view(tenant_id: str | None = None) -> dict:
    where = "TRUE" if tenant_id is None else f"h.tenant_id = '{tenant_id}'::uuid"
    incident_where = "TRUE" if tenant_id is None else f"tenant_id = '{tenant_id}'::uuid"
    panels = [
        _panel(1, "Hosts", "stat", {"x": 0, "y": 0, "w": 6, "h": 4}, [_target(f"SELECT count(*)::float AS value FROM hosts h WHERE {where}", "table")]),
        _panel(2, "CPU now", "stat", {"x": 6, "y": 0, "w": 6, "h": 4}, [_target(_agg_series("cpu_usage_percent", tenant_id, "cpu"), "time_series")], "percent"),
        _panel(3, "Open incidents", "stat", {"x": 12, "y": 0, "w": 6, "h": 4}, [_target(_count_sql("incidents", incident_where + " AND status NOT IN ('resolved','closed')" if tenant_id else "status NOT IN ('resolved','closed')"), "table")]),
        _panel(4, "Uptime", "stat", {"x": 18, "y": 0, "w": 6, "h": 4}, [_target(_agg_series("uptime_seconds", tenant_id, "uptime"), "time_series")], "s"),
        _panel(5, "Server comparison", "bargauge", {"x": 0, "y": 4, "w": 24, "h": 6}, [_target(_top_series("cpu_usage_percent", tenant_id))], "percent"),
        _panel(6, "Aggregate CPU", "timeseries", {"x": 0, "y": 10, "w": 8, "h": 7}, [_target(_agg_series("cpu_usage_percent", tenant_id, "cpu"))], "percent"),
        _panel(7, "Aggregate RAM", "timeseries", {"x": 8, "y": 10, "w": 8, "h": 7}, [_target(_agg_series("memory_usage_percent", tenant_id, "ram"))], "percent"),
        _panel(8, "Aggregate network", "timeseries", {"x": 16, "y": 10, "w": 8, "h": 7}, [_target(_agg_series("net_in_mbps", tenant_id, "network"))], "Mbits"),
        _panel(9, "Service availability", "state-timeline", {"x": 0, "y": 17, "w": 12, "h": 7}, [_target(_top_series("service_availability", tenant_id, 8))]),
        _panel(10, "Latency heatmap", "heatmap", {"x": 12, "y": 17, "w": 12, "h": 7}, [_target(_agg_series("service_latency_p95_ms", tenant_id, "p95"))], "ms"),
        _panel(11, "Servers", "table", {"x": 0, "y": 24, "w": 14, "h": 8}, [_target(f"""
SELECT h.hostname, h.id::text AS host_id, h.health_status AS status, h.role
FROM hosts h WHERE {where} ORDER BY h.hostname
""".strip(), "table")]),
        _panel(12, "Incidents", "table", {"x": 14, "y": 24, "w": 10, "h": 8}, [_target(
            f"SELECT title, severity, status, started_at FROM incidents WHERE {incident_where} ORDER BY started_at DESC LIMIT 20",
            "table",
        )]),
        _panel(13, "Status history", "status-history", {"x": 0, "y": 32, "w": 24, "h": 6}, [_target(_top_series("service_availability", tenant_id, 8))]),
    ]
    panel = panels[10]
    panel["fieldConfig"]["overrides"] = [{
        "matcher": {"id": "byName", "options": "hostname"},
        "properties": [{"id": "links", "value": [{"title": "Server", "url": "/grafana/d/hmc-resources?var-host=${__data.fields.host_id}&kiosk=tv&theme=dark"}]}],
    }, {"matcher": {"id": "byName", "options": "host_id"}, "properties": [{"id": "custom.hidden", "value": True}]}]
    return _dashboard("hmc-tenant", "Tenant", panels, tenant_id is None)


def resources(tenant_id: str | None = None) -> dict:
    panels = [
        _timeseries(1, "CPU", "cpu_usage_percent", {"x": 0, "y": 0, "w": 16, "h": 8}, tenant_id, None, "percent", True),
        _panel(2, "Memory now", "gauge", {"x": 16, "y": 0, "w": 8, "h": 8}, [_target(series_sql("memory_usage_percent", tenant_id, use_host_var=True))], "percent"),
        _timeseries(3, "Memory", "memory_usage_percent", {"x": 0, "y": 8, "w": 12, "h": 7}, tenant_id, None, "percent", True),
        _panel(4, "Disk", "bargauge", {"x": 12, "y": 8, "w": 12, "h": 7}, [_target(_top_series("disk_usage_percent", tenant_id))], "percent"),
        _timeseries(5, "Network", "net_in_mbps", {"x": 0, "y": 15, "w": 12, "h": 7}, tenant_id, None, "Mbits", True),
        _panel(6, "Availability", "state-timeline", {"x": 12, "y": 15, "w": 12, "h": 7}, [_target(_top_series("service_availability", tenant_id, 8))]),
        _panel(7, "Error counts", "barchart", {"x": 0, "y": 22, "w": 12, "h": 7}, [_target(f"""
SELECT h.hostname AS metric, count(*)::float AS value
FROM alert_events a JOIN hosts h ON h.id = a.host_id
WHERE a.state = 'firing' AND {_tenant('a', tenant_id)}
GROUP BY 1 ORDER BY 2 DESC LIMIT 10
""".strip(), "table")]),
        _panel(8, "Service status history", "status-history", {"x": 12, "y": 22, "w": 12, "h": 7}, [_target(_top_series("service_availability", tenant_id, 8))]),
        _logs_panel(9, {"x": 0, "y": 29, "w": 24, "h": 8}, tenant_id, ""),
    ]
    dash = _dashboard("hmc-resources", "Server", panels, tenant_id is None)
    where = "" if tenant_id is None else f" WHERE tenant_id = '{tenant_id}'::uuid"
    query = f"SELECT id::text AS __value, hostname AS __text FROM hosts{where} ORDER BY hostname"
    dash["templating"]["list"].append(
        {
            "name": "host",
            "label": "Host",
            "type": "query",
            "datasource": PG,
            "definition": query,
            "query": query,
            "includeAll": True,
            "allValue": "all",
            "refresh": 1,
            "current": {"selected": True, "text": "All", "value": "all"},
        }
    )
    return dash


def infrastructure(tenant_id: str | None = None) -> dict:
    panels = [
        _panel(1, "Host CPU", "bargauge", {"x": 0, "y": 0, "w": 24, "h": 6}, [_target(_top_series("cpu_usage_percent", tenant_id, 12))], "percent"),
        _panel(2, "VM CPU trend", "timeseries", {"x": 0, "y": 6, "w": 12, "h": 7}, [_target(_agg_series("cpu_usage_percent", tenant_id, "cpu"))], "percent"),
        _panel(3, "Availability", "state-timeline", {"x": 12, "y": 6, "w": 12, "h": 7}, [_target(_top_series("service_availability", tenant_id, 8))]),
        _panel(4, "Datacenters", "stat", {"x": 0, "y": 13, "w": 6, "h": 4}, [_target("SELECT count(*)::float AS value FROM datacenters", "table")]),
        _panel(5, "Datacenter list", "table", {"x": 6, "y": 13, "w": 18, "h": 6}, [_target("""
SELECT d.name AS datacenter, r.name AS region, count(h.id)::float AS hosts
FROM datacenters d
JOIN regions r ON r.id = d.region_id
LEFT JOIN clusters c ON c.datacenter_id = d.id
LEFT JOIN hosts h ON h.cluster_id = c.id
GROUP BY d.name, r.name
ORDER BY d.name
""".strip(), "table")]),
        _panel(6, "Relationships", "nodeGraph", {"x": 0, "y": 19, "w": 12, "h": 8}, [
            {**_target("""
SELECT h.id::text AS id, h.hostname AS title, h.health_status AS subtitle
FROM hosts h ORDER BY h.hostname LIMIT 40
""".strip(), "table"), "refId": "A"},
            {**_target("""
SELECT r.id::text AS id, r.from_id::text AS source, r.to_id::text AS target
FROM infrastructure_relationships r
WHERE r.relationship IN ('runs','depends_on','contains')
LIMIT 80
""".strip(), "table"), "refId": "B"},
        ]),
        _panel(7, "Storage latency", "heatmap", {"x": 12, "y": 19, "w": 12, "h": 8}, [_target(_agg_series("storage_latency_ms", tenant_id, "latency"))], "ms"),
        _panel(8, "Regions", "geomap", {"x": 0, "y": 27, "w": 24, "h": 8}, [_target("""
SELECT
  CASE r.code WHEN 'baku' THEN 40.4093 WHEN 'frankfurt' THEN 50.1109 WHEN 'istanbul' THEN 41.0082 END AS latitude,
  CASE r.code WHEN 'baku' THEN 49.8671 WHEN 'frankfurt' THEN 8.6821 WHEN 'istanbul' THEN 28.9784 END AS longitude,
  d.name AS name
FROM datacenters d JOIN regions r ON r.id = d.region_id
WHERE r.code IN ('baku','frankfurt','istanbul')
""".strip(), "table")]),
    ]
    panels[7]["options"] = {
        "view": {"id": "coords", "lat": 45, "lon": 30, "zoom": 4},
        "controls": {"showZoom": True, "mouseWheelZoom": True},
        "layers": [{"type": "markers", "location": {"mode": "auto"}, "config": {"showLegend": True}}],
    }
    return _dashboard("hmc-infrastructure", "Cloud infrastructure", panels, tenant_id is None)


def telephony(tenant_id: str | None = None) -> dict:
    panels = [
        _panel(1, "SIP registration", "state-timeline", {"x": 0, "y": 0, "w": 14, "h": 7}, [_target(_top_series("sip_registered", tenant_id, 8))]),
        _timeseries(2, "Active calls", "sip_active_calls", {"x": 14, "y": 0, "w": 10, "h": 7}, tenant_id, None, "short"),
        _panel(3, "Call setup success", "gauge", {"x": 0, "y": 7, "w": 6, "h": 6}, [_target(_agg_series("sip_setup_success_ratio", tenant_id, "success"))], "percentunit"),
        _panel(4, "SIP errors", "barchart", {"x": 6, "y": 7, "w": 8, "h": 6}, [_target(f"""
SELECT h.hostname AS metric, sum(m.value)::float AS value
FROM metric_samples m JOIN hosts h ON h.id = m.host_id
WHERE m.metric_name = 'sip_5xx_total' AND $__timeFilter(m."timestamp") AND {_tenant('m', tenant_id)}
GROUP BY 1 ORDER BY 2 DESC LIMIT 8
""".strip(), "table")]),
        _timeseries(5, "Jitter", "sip_jitter_ms", {"x": 14, "y": 7, "w": 10, "h": 6}, tenant_id, None, "ms"),
        _timeseries(6, "Packet loss", "sip_packet_loss_ratio", {"x": 0, "y": 13, "w": 12, "h": 6}, tenant_id, None, "percentunit"),
        _panel(7, "Call quality", "heatmap", {"x": 12, "y": 13, "w": 12, "h": 6}, [_target(_agg_series("sip_jitter_ms", tenant_id, "jitter"))], "ms"),
        _panel(8, "SIP trunks", "table", {"x": 0, "y": 19, "w": 24, "h": 7}, [_target(f"""
SELECT h.hostname AS trunk, h.health_status AS status
FROM hosts h WHERE h.role = 'pbx' AND {_tenant('h', tenant_id)}
ORDER BY h.hostname
""".strip(), "table")]),
    ]
    return _dashboard("hmc-telephony", "IP Telephony", panels, tenant_id is None)


def mail(tenant_id: str | None = None) -> dict:
    panels = [
        _timeseries(1, "Queue size", "mail_queue_size", {"x": 0, "y": 0, "w": 14, "h": 7}, tenant_id, None, "short"),
        _panel(2, "SMTP and IMAP", "status-history", {"x": 14, "y": 0, "w": 10, "h": 7}, [_target(_top_series("service_availability", tenant_id, 6))]),
        _panel(3, "Delivery success", "gauge", {"x": 0, "y": 7, "w": 6, "h": 6}, [_target(_agg_series("mail_delivery_rate", tenant_id, "delivery"))], "short"),
        _panel(4, "Deferred and rejected", "barchart", {"x": 6, "y": 7, "w": 8, "h": 6}, [_target(f"""
SELECT m.metric_name AS metric, sum(m.value)::float AS value
FROM metric_samples m
WHERE m.metric_name IN ('mail_deferred_total','mail_rejected_total')
  AND $__timeFilter(m."timestamp") AND {_tenant('m', tenant_id)}
GROUP BY 1
""".strip(), "table")]),
        _panel(5, "Queue delay", "histogram", {"x": 14, "y": 7, "w": 10, "h": 6}, [_target(f"""
SELECT m.value AS delay_seconds
FROM metric_samples m
WHERE m.metric_name = 'mail_oldest_age_seconds' AND $__timeFilter(m."timestamp") AND {_tenant('m', tenant_id)}
LIMIT 5000
""".strip(), "table")], "s"),
        _panel(6, "Mail disk", "bargauge", {"x": 0, "y": 13, "w": 24, "h": 6}, [_target(_top_series("disk_usage_percent", tenant_id, 8))], "percent"),
        _logs_panel(7, {"x": 0, "y": 19, "w": 24, "h": 7}, tenant_id, '{component=~"postfix|dovecot"}'),
    ]
    return _dashboard("hmc-mail", "Mail hosting", panels, tenant_id is None)


def logs_and_metrics(tenant_id: str | None = None) -> dict:
    panels = [
        _timeseries(1, "CPU", "cpu_usage_percent", {"x": 0, "y": 0, "w": 24, "h": 8}, tenant_id, None, "percent"),
        _logs_panel(2, {"x": 0, "y": 8, "w": 24, "h": 12}, tenant_id, ""),
    ]
    return _dashboard("hmc-logs", "Logs and metrics", panels, tenant_id is None)


def alerts(tenant_id: str | None = None) -> dict:
    where = "TRUE" if tenant_id is None else f"tenant_id = '{tenant_id}'::uuid"
    panels = [
        _panel(1, "Active alerts", "stat", {"x": 0, "y": 0, "w": 8, "h": 4}, [_target(_count_sql("alert_events", where + " AND state IN ('firing','pending')"), "table")]),
        _panel(2, "Open incidents", "stat", {"x": 8, "y": 0, "w": 8, "h": 4}, [_target(_count_sql("incidents", where + " AND status NOT IN ('resolved','closed')"), "table")]),
        _panel(3, "Alert history", "state-timeline", {"x": 16, "y": 0, "w": 8, "h": 8}, [_target(f"""
SELECT $__timeGroupAlias(started_at, '15m'), state AS metric, count(*)::float AS value
FROM alert_events WHERE {where} AND $__timeFilter(started_at)
GROUP BY 1, 2 ORDER BY 1
""".strip())]),
        _panel(4, "Incident frequency", "barchart", {"x": 0, "y": 8, "w": 12, "h": 7}, [_target(f"""
SELECT to_char(date_trunc('day', started_at), 'YYYY-MM-DD') AS metric, count(*)::float AS value
FROM incidents WHERE {where} AND $__timeFilter(started_at)
GROUP BY 1 ORDER BY 1
""".strip(), "table")]),
        _panel(5, "Severity", "piechart", {"x": 12, "y": 8, "w": 12, "h": 7}, [_target(f"""
SELECT severity AS metric, count(*)::float AS value FROM incidents WHERE {where} GROUP BY 1
""".strip(), "table")]),
        _panel(6, "Incidents", "table", {"x": 0, "y": 15, "w": 24, "h": 8}, [_target(
            f"SELECT title, status, severity, hypothesis, started_at FROM incidents WHERE {where} ORDER BY started_at DESC LIMIT 30",
            "table",
        )]),
        _logs_panel(7, {"x": 0, "y": 23, "w": 24, "h": 7}, tenant_id, '{severity=~"ERROR|CRITICAL"}'),
    ]
    return _dashboard("hmc-alerts", "Alerts and incidents", panels, False)


def scenario(tenant_id: str | None = None) -> dict:
    panels = [
        _timeseries(1, "Scenario metrics", "cpu_usage_percent", {"x": 0, "y": 0, "w": 16, "h": 8}, tenant_id, None, "percent", True),
        _panel(2, "Current CPU", "gauge", {"x": 16, "y": 0, "w": 8, "h": 8}, [_target(series_sql("cpu_usage_percent", tenant_id, use_host_var=True))], "percent"),
        _panel(3, "Scenario stages", "state-timeline", {"x": 0, "y": 8, "w": 16, "h": 6}, [_target("""
SELECT created_at AS time, phase AS metric, 1::float AS value
FROM scenario_events
WHERE $__timeFilter(created_at)
ORDER BY created_at
""".strip())]),
        _panel(4, "Active alerts", "stat", {"x": 16, "y": 8, "w": 8, "h": 6}, [_target("SELECT count(*)::float AS value FROM alert_events WHERE state = 'firing'", "table")]),
        _logs_panel(5, {"x": 0, "y": 14, "w": 14, "h": 8}, tenant_id, '{severity=~"WARNING|ERROR|CRITICAL"}'),
        _panel(6, "Detected incidents", "table", {"x": 14, "y": 14, "w": 10, "h": 8}, [_target(
            "SELECT title, status, severity, started_at FROM incidents ORDER BY started_at DESC LIMIT 15",
            "table",
        )]),
    ]
    return _dashboard("hmc-scenario", "Scenario lab", panels, tenant_id is None)


def platform() -> dict:
    def prom(panel_id: int, title: str, expr: str, grid: dict, unit: str) -> dict:
        return _panel(
            panel_id,
            title,
            "timeseries",
            grid,
            [{"refId": "A", "datasource": PROM, "expr": expr}],
            unit,
            PROM,
        )

    panels = [
        prom(1, "API request rate", "sum by (handler) (rate(hmc_http_requests_total[1m]))", {"x": 0, "y": 0, "w": 12, "h": 8}, "reqps"),
        prom(2, "API latency p95", "histogram_quantile(0.95, sum by (le, handler) (rate(hmc_http_request_duration_seconds_bucket[5m])))", {"x": 12, "y": 0, "w": 12, "h": 8}, "s"),
        prom(3, "Scrape up", "up", {"x": 0, "y": 8, "w": 24, "h": 6}, "short"),
    ]
    return _dashboard("hmc-platform", "Platform health", panels, False)


def _logs_panel(panel_id: int, grid: dict, tenant_id: str | None, selector: str) -> dict:
    expr = selector or "{}"
    if tenant_id and selector:
        expr = selector[:-1] + f', tenant_id="{tenant_id}"' + "}"
    elif tenant_id:
        expr = f'{{tenant_id="{tenant_id}"}}'
    return _panel(panel_id, "Logs", "logs", grid, [{"refId": "A", "datasource": LOKI, "expr": expr}], None, LOKI)


def _dashboard(uid: str, title: str, panels: list, with_tenant_var: bool) -> dict:
    return {
        "uid": uid,
        "title": title,
        "timezone": "browser",
        "editable": False,
        "graphTooltip": 1,
        "schemaVersion": 39,
        "refresh": "5s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": templating(with_tenant_var),
        "panels": panels,
    }


def platform_dashboards() -> list[dict]:
    return [overview(), tenant_view(), resources(), infrastructure(), telephony(), mail(), logs_and_metrics(), alerts(), scenario(), platform()]


def tenant_dashboards(tenant_id: str) -> list[dict]:
    return [
        overview(tenant_id),
        tenant_view(tenant_id),
        resources(tenant_id),
        infrastructure(tenant_id),
        telephony(tenant_id),
        mail(tenant_id),
        logs_and_metrics(tenant_id),
        alerts(tenant_id),
        scenario(tenant_id),
    ]
