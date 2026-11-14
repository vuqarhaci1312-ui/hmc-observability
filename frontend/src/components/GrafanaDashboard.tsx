import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { rangeToIso } from "../lib/time";
import { useUi } from "../stores/ui";
import { StateBlock } from "./ui";

export function GrafanaDashboard({ dashboard, tenantId, vars }: { dashboard: string; tenantId?: string; vars?: Record<string, string> }) {
  const preset = useUi((state) => state.preset);
  const selectedTenant = useUi((state) => state.tenantId);
  const tenant = tenantId ?? selectedTenant;
  const range = rangeToIso(preset);
  const session = useQuery({
    queryKey: ["grafana-session", dashboard, tenant],
    queryFn: () =>
      api<{ org_id: number; dashboard: string }>("/api/v1/grafana/session", {
        method: "POST",
        body: JSON.stringify({ dashboard, tenant_id: tenant || null }),
      }),
  });
  if (session.isLoading) return <StateBlock kind="loading" message="Grafana açılır" />;
  if (session.isError || !session.data) {
    return <StateBlock kind="error" message={session.error instanceof Error ? session.error.message : "Grafana açılmadı"} onRetry={() => session.refetch()} />;
  }
  const params = new URLSearchParams({
    orgId: String(session.data.org_id),
    kiosk: "tv",
    theme: "dark",
    refresh: "1m",
  });
  if (range.start) params.set("from", String(Date.parse(range.start)));
  if (range.end) params.set("to", String(Date.parse(range.end)));
  if (tenant) params.set("var-tenant", tenant);
  Object.entries(vars ?? {}).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
    return <iframe title={dashboard} src={`/grafana/d/${dashboard}?${params.toString()}`} className="h-[calc(100vh-3.25rem)] w-full border-0 bg-[#111217]" />;
}
