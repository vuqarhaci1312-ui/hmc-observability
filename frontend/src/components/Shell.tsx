import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  Activity,
  Bell,
  Cloud,
  Database,
  GitFork,
  HeartPulse,
  LayoutDashboard,
  LineChart,
  Mail,
  Network,
  Phone,
  Radio,
  ScrollText,
  Search,
  Server,
  Settings,
  Siren,
  Boxes,
  FlaskConical,
  Building2,
  type LucideIcon,
} from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQuery } from "@tanstack/react-query";
import { api, queryString } from "../lib/api";
import { useUi } from "../stores/ui";
import { useEventStream } from "../hooks/useEvents";
import { Field, Select } from "./ui";
import { useState } from "react";

const links: { path: string; label: string; icon: LucideIcon }[] = [
  { path: "/", label: "Ümumi baxış", icon: LayoutDashboard },
  { path: "/tenants", label: "Tenantlar", icon: Building2 },
  { path: "/infrastructure", label: "İnfrastruktur", icon: Network },
  { path: "/hosts", label: "Serverlər", icon: Server },
  { path: "/services", label: "Xidmətlər", icon: Boxes },
  { path: "/telephony", label: "IP telefoniya", icon: Phone },
  { path: "/mail", label: "Mail hosting", icon: Mail },
  { path: "/cloud", label: "Cloud monitoring", icon: Cloud },
  { path: "/logs", label: "Log mərkəzi", icon: ScrollText },
  { path: "/metrics", label: "Metriklər", icon: LineChart },
  { path: "/alerts", label: "Alertlər", icon: Bell },
  { path: "/incidents", label: "İnsidentlər", icon: Siren },
  { path: "/topology", label: "Topologiya", icon: GitFork },
  { path: "/scenarios", label: "Simulyasiya laboratoriyası", icon: FlaskConical },
  { path: "/health", label: "Sistem sağlamlığı", icon: HeartPulse },
  { path: "/datasources", label: "Məlumat mənbələri", icon: Database },
  { path: "/admin", label: "Ayarlar", icon: Settings },
];

export function Shell() {
  const ui = useUi();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  useEventStream();
  const tenants = useQuery({
    queryKey: ["tenants", ui.mode],
    queryFn: () => api<{ items: { id: string; name: string }[] }>(`/api/v1/tenants${queryString({ mode: ui.mode, page_size: 100 })}`),
  });
  const notes = useQuery({
    queryKey: ["notes"],
    queryFn: () => api<{ items: { title: string; body: string }[] }>("/api/v1/notifications"),
  });

  return (
    <div className="grid h-full grid-cols-[56px_1fr]">
      <aside className="flex flex-col border-r border-line bg-[#111217]">
        <div className="flex h-10 items-center justify-center border-b border-line text-[10px] font-semibold tracking-wide text-muted" title="HMC Observability">
          HMC
        </div>
        <nav className="flex flex-1 flex-col items-center gap-1 overflow-auto py-2">
          {links.map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              title={label}
              className={({ isActive }) =>
                `flex h-9 w-9 items-center justify-center rounded border-l-2 ${isActive ? "border-accent bg-elevated text-white" : "border-transparent text-muted hover:bg-elevated hover:text-ink"}`
              }
            >
              <Icon className="h-4 w-4" />
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex h-10 items-center gap-2 border-b border-line bg-surface px-3">
          <form
            className="relative min-w-40 flex-1"
            onSubmit={(event) => {
              event.preventDefault();
              navigate(`/search?q=${encodeURIComponent(search)}`);
            }}
          >
            <Search className="absolute left-2 top-1.5 h-3.5 w-3.5 text-muted" />
            <Field className="h-7 py-0 pl-7 text-xs" placeholder="Axtarış" value={search} onChange={(event) => setSearch(event.target.value)} />
          </form>
          <Select className="h-7 py-0 text-xs" value={ui.tenantId} onChange={(event) => ui.setTenantId(event.target.value)}>
            <option value="">Bütün tenantlar</option>
            {tenants.data?.items.map((tenant) => <option key={tenant.id} value={tenant.id}>{tenant.name}</option>)}
          </Select>
          <Select className="h-7 py-0 text-xs" value={ui.preset} onChange={(event) => ui.setPreset(event.target.value)}>
            {["5m", "15m", "1h", "6h", "24h", "7d", "30d"].map((item) => <option key={item} value={item}>{item}</option>)}
          </Select>
          <Select className="h-7 py-0 text-xs" value={ui.mode} onChange={(event) => ui.setMode(event.target.value as "demo" | "real" | "mixed")}>
            <option value="demo">Demo</option>
            <option value="real">Real</option>
            <option value="mixed">Mixed</option>
          </Select>
          <span className="inline-flex items-center gap-1 text-[11px] text-muted" title={ui.live ? "Live" : "Bağlantı yoxdur"}>
            <Radio className={ui.live ? "h-3.5 w-3.5 text-healthy" : "h-3.5 w-3.5 text-critical"} />
          </span>
          <span className="inline-flex items-center gap-1 text-[11px]" title={notes.data?.items[0]?.body || "Bildirişlər"}>
            <Bell className="h-3.5 w-3.5" /> {notes.data?.items.length ?? 0}
          </span>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger className="rounded border border-line px-2 py-0.5 text-[11px]" title="Tema">{ui.theme}</DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className="rounded border border-line bg-elevated p-1 text-sm">
                <DropdownMenu.Item className="cursor-pointer px-2 py-1" onSelect={() => ui.setTheme("dark")}>Dark</DropdownMenu.Item>
                <DropdownMenu.Item className="cursor-pointer px-2 py-1" onSelect={() => ui.setTheme("light")}>Light</DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
          <span className="max-w-28 truncate text-[11px]" title={ui.user?.full_name}>{ui.user?.full_name}</span>
          <button className="text-[11px] text-accent" onClick={async () => { await api("/api/v1/auth/logout", { method: "POST" }); ui.setToken(null); ui.setUser(null); navigate("/login"); }}>Çıxış</button>
        </header>
        <main className="min-h-0 flex-1 overflow-auto p-2">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function StatusDot({ status }: { status?: string }) {
  const color = status === "healthy" || status === "up" ? "bg-healthy" : status === "warning" ? "bg-warning" : status === "critical" || status === "firing" || status === "down" ? "bg-critical" : "bg-info";
  return <span className="inline-flex items-center gap-1 text-xs"><span className={`h-2 w-2 rounded-full ${color}`} /><Activity className="h-3 w-3" />{status || "unknown"}</span>;
}
