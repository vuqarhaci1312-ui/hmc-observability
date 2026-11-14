import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, Controls, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, queryString } from "../lib/api";
import { formatTime, rangeToIso } from "../lib/time";
import { useUi } from "../stores/ui";
import { Badge, Button, Card, Field, StateBlock } from "../components/ui";
import { DataTable } from "../components/DataTable";
import type { ColumnDef } from "@tanstack/react-table";
import { GrafanaDashboard } from "../components/GrafanaDashboard";

function useScope() {
  const ui = useUi();
  const range = rangeToIso(ui.preset);
  return { mode: ui.mode, tenant_id: ui.tenantId || undefined, start: range.start || undefined, end: range.end || undefined };
}

export function ServicesPage() {
  const scope = useScope();
  const query = useQuery({ queryKey: ["services", scope], queryFn: () => api<{ items: { id: string; name: string; kind: string; source_type: string; health_status: string }[]; no_connected_sources: boolean }>(`/api/v1/services${queryString(scope)}`) });
  if (query.isLoading) return <StateBlock kind="loading" message="Xidmətlər yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  if (query.data?.no_connected_sources) return <StateBlock kind="nodata" message="No connected sources" />;
  return <Card title="Xidmətlər"><ul className="space-y-1 text-sm">{query.data?.items.map((item) => <li key={item.id}><Link className="text-accent" to={`/services/${item.id}`}>{item.name}</Link> · {item.kind} · <Badge>{item.source_type}</Badge></li>)}</ul></Card>;
}

export function ServiceDetailPage() {
  const { id } = useParams();
  const query = useQuery({ queryKey: ["service", id], queryFn: () => api<{ service: { name: string; kind: string; health_status: string; source_type: string }; host_health: string }>(`/api/v1/services/${id}/health`) });
  if (query.isLoading) return <StateBlock kind="loading" message="Servis yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  return <Card title={query.data?.service.name || "Servis"}><p>{query.data?.service.kind}</p><p>Host: {query.data?.host_health}</p><Badge>{query.data?.service.source_type}</Badge></Card>;
}

export function DomainPage({ name }: { name: string; title: string }) {
  const dashboard = name === "mail" ? "hmc-mail" : name === "telephony" ? "hmc-telephony" : "hmc-infrastructure";
  return <GrafanaDashboard dashboard={dashboard} />;
}

export function LogsPage() {
  return <GrafanaDashboard dashboard="hmc-logs" />;
}

export function MetricsPage() {
  return <GrafanaDashboard dashboard="hmc-resources" />;
}

export function AlertsPage() {
  const scope = useScope();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["alerts", scope.tenant_id], queryFn: () => api<{ items: { id: string; title: string; state: string; severity: string; started_at: string }[] }>(`/api/v1/alerts${queryString({ tenant_id: scope.tenant_id })}`) });
  const ack = useMutation({ mutationFn: (id: string) => api(`/api/v1/alerts/${id}/acknowledge`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["alerts"] }) });
  if (query.isLoading) return <StateBlock kind="loading" message="Alertlər yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  const columns: ColumnDef<{ id: string; title: string; state: string; severity: string; started_at: string }, unknown>[] = [
    { accessorKey: "title", header: "Başlıq" },
    { accessorKey: "state", header: "Vəziyyət", cell: ({ row }) => <Badge status={row.original.state}>{row.original.state}</Badge> },
    { accessorKey: "started_at", header: "Vaxt", cell: ({ row }) => formatTime(row.original.started_at) },
    { id: "ack", header: "", cell: ({ row }) => row.original.state === "firing" ? <Button onClick={() => ack.mutate(row.original.id)}>Təsdiqlə</Button> : null },
  ];
  return (
    <div className="space-y-4">
      <GrafanaDashboard dashboard="hmc-alerts" />
      <Card title="Təsdiq">{query.data?.items.length ? <DataTable data={query.data.items} columns={columns} /> : <StateBlock kind="empty" message="Alert yoxdur" />}</Card>
    </div>
  );
}

export function IncidentsPage() {
  const scope = useScope();
  const query = useQuery({ queryKey: ["incidents", scope.tenant_id], queryFn: () => api<{ items: { id: string; title: string; status: string; severity: string; hypothesis: string; started_at: string }[] }>(`/api/v1/incidents${queryString({ tenant_id: scope.tenant_id })}`) });
  if (query.isLoading) return <StateBlock kind="loading" message="İnsidentlər yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  return (
    <div className="space-y-4">
      <GrafanaDashboard dashboard="hmc-alerts" />
      <Card title="İnsident əməliyyatı"><ul className="space-y-2">{query.data?.items.map((item) => <li key={item.id} className="border-b border-line py-2"><Link className="text-accent" to={`/incidents/${item.id}`}>{item.title}</Link><div className="text-xs text-muted"><Badge status={item.status}>{item.status}</Badge> {item.hypothesis}</div><div className="text-xs">{formatTime(item.started_at)}</div></li>)}{!query.data?.items.length && <StateBlock kind="empty" message="İnsident yoxdur" />}</ul></Card>
    </div>
  );
}

export function IncidentDetailPage() {
  const { id } = useParams();
  const client = useQueryClient();
  const [comment, setComment] = useState("");
  const query = useQuery({ queryKey: ["incident", id], queryFn: () => api<{ title: string; status: string; hypothesis: string; cause_confirmed: boolean; timeline: { kind: string; message: string; at: string }[] }>(`/api/v1/incidents/${id}`) });
  const act = useMutation({ mutationFn: (path: string) => api(`/api/v1/incidents/${id}/${path}`, { method: "POST", body: path === "comments" ? JSON.stringify({ body: comment }) : "{}" }), onSuccess: () => client.invalidateQueries({ queryKey: ["incident", id] }) });
  if (query.isLoading) return <StateBlock kind="loading" message="İnsident yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  const item = query.data;
  return (
    <div className="space-y-4">
      <Card title={item?.title}>
        <Badge status={item?.status}>{item?.status}</Badge>
        <p className="mt-2 text-sm">{item?.cause_confirmed ? "Təsdiqlənmiş səbəb" : "Probable root cause"}: {item?.hypothesis}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => act.mutate("acknowledge")}>Təsdiqlə</Button>
          <Button onClick={() => act.mutate("resolve")}>Həll et</Button>
          <Button onClick={() => act.mutate("reopen")}>Yenidən aç</Button>
          <Button onClick={() => act.mutate("close")}>Bağla</Button>
        </div>
        <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); act.mutate("comments"); }}>
          <Field value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Şərh" />
          <Button type="submit">Əlavə et</Button>
        </form>
      </Card>
      <Card title="Timeline"><ul className="space-y-2 text-sm">{item?.timeline.map((event, index) => <li key={index}><span className="text-muted">{formatTime(event.at)}</span> {event.kind}: {event.message}</li>)}</ul></Card>
    </div>
  );
}

export function TopologyPage() {
  const scope = useScope();
  const query = useQuery({ queryKey: ["topology", scope.tenant_id], queryFn: () => api<{ nodes: { id: string; hostname: string; health_status: string; role: string }[]; edges: { from_id: string; to_id: string; relationship: string }[] }>(`/api/v1/topology${queryString({ tenant_id: scope.tenant_id })}`) });
  if (query.isLoading) return <StateBlock kind="loading" message="Topologiya yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  const nodes = (query.data?.nodes || []).slice(0, 80).map((node, index) => ({ id: node.id, position: { x: (index % 8) * 180, y: Math.floor(index / 8) * 90 }, data: { label: `${node.role}: ${node.hostname}` }, style: { background: "#182235", color: "#F3F6FA", border: "1px solid #283548", fontSize: 11, width: 160 } }));
  const edges = (query.data?.edges || []).filter((edge) => nodes.some((node) => node.id === edge.from_id) && nodes.some((node) => node.id === edge.to_id)).map((edge, index) => ({ id: `${edge.from_id}-${edge.to_id}-${index}`, source: edge.from_id, target: edge.to_id, label: edge.relationship }));
  return <div className="h-[720px] rounded-lg border border-line"><ReactFlow nodes={nodes} edges={edges} fitView><Background /><Controls /></ReactFlow></div>;
}

export function ScenarioPage() {
  const client = useQueryClient();
  const scope = useScope();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () => api<{ items: { code: string; name: string; description: string; severity: string; symptoms: string[]; duration_seconds: number; infrastructure_type: string }[] }>("/api/v1/scenarios") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<{ items: { id: string; status: string; phase: string; speed: number; virtual_elapsed_seconds: number; real_elapsed_seconds: number; evaluation: Record<string, unknown> }[] }>("/api/v1/scenario-runs"), refetchInterval: 3000 });
  const start = useMutation({ mutationFn: (code: string) => api("/api/v1/scenarios/run", { method: "POST", body: JSON.stringify({ code, tenant_id: scope.tenant_id, speed: 20 }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }) });
  const control = useMutation({ mutationFn: ({ id, action }: { id: string; action: string }) => api(`/api/v1/scenario-runs/${id}/${action}`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }) });
  if (scenarios.isLoading) return <StateBlock kind="loading" message="Ssenarilər yüklənir" />;
  if (scenarios.isError) return <StateBlock kind="error" message={(scenarios.error as Error).message} onRetry={() => scenarios.refetch()} />;
  return (
    <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
      <Card title="Ssenari kataloqu">
        <ul className="space-y-3 text-sm">
          {scenarios.data?.items.map((item) => (
            <li key={item.code} className="rounded border border-line p-2">
              <div className="font-medium">{item.name}</div>
              <p className="text-xs text-muted">{item.description}</p>
              <div className="mt-1 text-xs">{item.severity} · {item.infrastructure_type} · {item.duration_seconds}s</div>
              <Button className="mt-2" tone="primary" onClick={() => start.mutate(item.code)}>Başlat</Button>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="İcra">
        <ul className="space-y-3 text-sm">
          {runs.data?.items.map((run) => (
            <li key={run.id} className="rounded border border-line p-3">
              <div className="flex flex-wrap items-center gap-2"><Badge status={run.status}>{run.status}</Badge><span>{run.phase}</span><span className="text-muted">virtual {Math.round(run.virtual_elapsed_seconds)}s / real {Math.round(run.real_elapsed_seconds || 0)}s / {run.speed}x</span></div>
              <div className="mt-2 flex gap-2">
                <Button onClick={() => control.mutate({ id: run.id, action: "pause" })}>Fasilə</Button>
                <Button onClick={() => control.mutate({ id: run.id, action: "resume" })}>Davam</Button>
                <Button onClick={() => control.mutate({ id: run.id, action: "stop" })}>Stop</Button>
                <Button onClick={() => control.mutate({ id: run.id, action: "replay" })}>Replay</Button>
              </div>
              {run.evaluation && Object.keys(run.evaluation).length > 0 && <pre className="mt-2 overflow-auto text-xs text-muted">{JSON.stringify(run.evaluation, null, 2)}</pre>}
            </li>
          ))}
          {!runs.data?.items.length && <StateBlock kind="empty" message="Aktiv ssenari yoxdur" />}
        </ul>
      </Card>
      <div className="xl:col-span-2">
        <GrafanaDashboard dashboard="hmc-scenario" />
      </div>
    </div>
  );
}

export function HealthPage() {
  return <GrafanaDashboard dashboard="hmc-platform" />;
}

export function DataSourcesPage() {
  const query = useQuery({ queryKey: ["datasources"], queryFn: () => api<{ items: { provider?: string; name?: string; status: string; message?: string }[] }>("/api/v1/datasources") });
  if (query.isLoading) return <StateBlock kind="loading" message="Mənbələr yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  return <Card title="Məlumat mənbələri"><ul className="space-y-2 text-sm">{query.data?.items.map((item, index) => <li key={index} className="flex justify-between gap-3 border-b border-line py-2"><span>{item.provider || item.name}</span><Badge status={item.status}>{item.status}</Badge><span className="text-muted">{item.message}</span></li>)}</ul></Card>;
}

export function AdminPage() {
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<{ items: { id: string; email: string; role: string }[] }>("/api/v1/admin/users") });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api<{ items: { action: string; actor: string; at: string }[] }>("/api/v1/system/audit") });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<{ items: { key: string; value: unknown }[] }>("/api/v1/system/settings") });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="İstifadəçilər">{users.isError ? <StateBlock kind="error" message="İstifadəçi siyahısı üçün super admin lazımdır" /> : <ul>{users.data?.items.map((user) => <li key={user.id}>{user.email} · {user.role}</li>)}</ul>}</Card>
      <Card title="Audit"><ul className="text-sm">{audit.data?.items.slice(0, 20).map((item, index) => <li key={index}>{formatTime(item.at)} {item.action} {item.actor}</li>)}</ul></Card>
      <Card title="Ayarlar"><pre className="text-xs">{JSON.stringify(settings.data?.items || [], null, 2)}</pre></Card>
    </div>
  );
}

export function InfrastructurePage() {
  return <GrafanaDashboard dashboard="hmc-infrastructure" />;
}

export function SearchPage() {
  const params = new URLSearchParams(window.location.search);
  const q = params.get("q") || "";
  const query = useQuery({ queryKey: ["search", q], enabled: Boolean(q), queryFn: () => api<{ tenants: { id: string; name: string }[]; hosts: { id: string; hostname: string }[] }>(`/api/v1/search?q=${encodeURIComponent(q)}`) });
  if (!q) return <StateBlock kind="empty" message="Axtarış mətni yazın" />;
  if (query.isLoading) return <StateBlock kind="loading" message="Axtarılır" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  return <Card title={`Nəticə: ${q}`}><div>{query.data?.tenants.map((item) => <div key={item.id}><Link className="text-accent" to={`/tenants/${item.id}`}>{item.name}</Link></div>)}{query.data?.hosts.map((item) => <div key={item.id}><Link className="text-accent" to={`/hosts/${item.id}`}>{item.hostname}</Link></div>)}</div></Card>;
}
