import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { api, queryString } from "../lib/api";
import { useUi } from "../stores/ui";
import { rangeToIso, formatTime } from "../lib/time";
import { Badge, Card, StateBlock } from "../components/ui";
import { StatusDot } from "../components/Shell";
import { GrafanaDashboard } from "../components/GrafanaDashboard";

function useScope() {
  const ui = useUi();
  const range = rangeToIso(ui.preset);
  return { mode: ui.mode, tenant_id: ui.tenantId || undefined, start: range.start || undefined, end: range.end || undefined };
}

export function OverviewPage() {
  return <GrafanaDashboard dashboard="hmc-overview" />;
}

export function TenantsPage() {
  const scope = useScope();
  const query = useQuery({ queryKey: ["tenant-table", scope.mode], queryFn: () => api<{ items: Record<string, unknown>[]; no_connected_sources: boolean }>(`/api/v1/tenants${queryString({ mode: scope.mode })}`) });
  if (query.isLoading) return <StateBlock kind="loading" message="Tenantlar yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  if (query.data?.no_connected_sources) return <StateBlock kind="nodata" message="No connected sources" />;
  return (
    <Card title="Tenantlar">
      <table className="w-full text-left text-sm">
        <thead className="text-muted"><tr><th>Ad</th><th>Mühit</th><th>Host</th><th>Servis</th><th>Sağlamlıq</th><th>SLA</th><th>Mənbə</th></tr></thead>
        <tbody>
          {query.data?.items.map((item) => (
            <tr key={String(item.id)} className="border-t border-line">
              <td className="py-2"><Link className="text-accent" to={`/tenants/${item.id}`}>{String(item.name)}</Link></td>
              <td>{String(item.environment)}</td>
              <td>{String(item.hosts)}</td>
              <td>{String(item.services)}</td>
              <td><StatusDot status={(item.health as { status?: string })?.status} /></td>
              <td>{String(item.sla)}</td>
              <td><Badge>{String(item.source_type)}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

export function TenantDetailPage() {
  const { id } = useParams();
  return <GrafanaDashboard dashboard="hmc-tenant" tenantId={id} />;
}

export function HostsPage() {
  const scope = useScope();
  const query = useQuery({ queryKey: ["hosts", scope], queryFn: () => api<{ items: Record<string, string>[]; no_connected_sources: boolean }>(`/api/v1/hosts${queryString(scope)}`) });
  if (query.isLoading) return <StateBlock kind="loading" message="Serverlər yüklənir" />;
  if (query.isError) return <StateBlock kind="error" message={(query.error as Error).message} onRetry={() => query.refetch()} />;
  if (query.data?.no_connected_sources) return <StateBlock kind="nodata" message="No connected sources" />;
  return (
    <div className="space-y-4">
    <GrafanaDashboard dashboard="hmc-infrastructure" />
    <Card title="Serverlər">
      <table className="w-full text-left text-sm">
        <thead className="text-muted"><tr><th>Hostname</th><th>IP</th><th>Rol</th><th>Status</th><th>Mənbə</th><th>Son görünmə</th></tr></thead>
        <tbody>
          {query.data?.items.map((host) => (
            <tr key={host.id} className="border-t border-line">
              <td className="py-2"><Link className="text-accent" to={`/hosts/${host.id}`}>{host.hostname}</Link></td>
              <td>{host.ip_address}</td>
              <td>{host.role}</td>
              <td><StatusDot status={host.health_status} /></td>
              <td><Badge>{host.source_type}</Badge></td>
              <td>{formatTime(host.last_seen_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
    </div>
  );
}

export function HostDetailPage() {
  const { id } = useParams();
  const host = useQuery({ queryKey: ["host", id], queryFn: () => api<Record<string, string>>(`/api/v1/hosts/${id}`) });
  const services = useQuery({ queryKey: ["host-services", id], queryFn: () => api<{ items: { id: string; name: string; kind: string }[] }>(`/api/v1/hosts/${id}/services`) });
  if (host.isLoading) return <StateBlock kind="loading" message="Host yüklənir" />;
  if (host.isError) return <StateBlock kind="error" message={(host.error as Error).message} onRetry={() => host.refetch()} />;
  const item = host.data || {};
  return (
    <div className="space-y-4">
      <Card title={item.hostname}>
        <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
          <div>IP: {item.ip_address}</div>
          <div>OS: {item.os_name}</div>
          <div>Status: <StatusDot status={item.health_status} /></div>
          <div>Mənbə: <Badge>{item.source_type}</Badge></div>
          <div>Son görünmə: {formatTime(item.last_seen_at)}</div>
          <div>CPU cores: {item.cpu_cores}</div>
        </div>
      </Card>
      <GrafanaDashboard dashboard="hmc-resources" vars={id ? { "var-host": id } : undefined} />
      <Card title="Servislər">
        <ul>{services.data?.items.map((service) => <li key={service.id}><Link className="text-accent" to={`/services/${service.id}`}>{service.name}</Link> · {service.kind}</li>)}</ul>
      </Card>
    </div>
  );
}
