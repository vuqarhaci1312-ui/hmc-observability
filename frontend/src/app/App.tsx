import { Navigate, Route, Routes } from "react-router-dom";
import { useUi } from "../stores/ui";
import { Shell } from "../components/Shell";
import { LoginPage } from "../features/LoginPage";
import { HostDetailPage, HostsPage, OverviewPage, TenantDetailPage, TenantsPage } from "../features/corePages";
import { AdminPage, AlertsPage, DataSourcesPage, DomainPage, HealthPage, IncidentDetailPage, IncidentsPage, InfrastructurePage, LogsPage, MetricsPage, ScenarioPage, SearchPage, ServiceDetailPage, ServicesPage, TopologyPage } from "../features/operationsPages";

function Private() {
  const token = useUi((state) => state.token);
  if (!token) return <Navigate to="/login" replace />;
  return <Shell />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Private />}>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/tenants" element={<TenantsPage />} />
        <Route path="/tenants/:id" element={<TenantDetailPage />} />
        <Route path="/infrastructure" element={<InfrastructurePage />} />
        <Route path="/hosts" element={<HostsPage />} />
        <Route path="/hosts/:id" element={<HostDetailPage />} />
        <Route path="/services" element={<ServicesPage />} />
        <Route path="/services/:id" element={<ServiceDetailPage />} />
        <Route path="/telephony" element={<DomainPage name="telephony" title="IP telefoniya" />} />
        <Route path="/mail" element={<DomainPage name="mail" title="Mail hosting" />} />
        <Route path="/cloud" element={<DomainPage name="cloud" title="Cloud monitoring" />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/incidents/:id" element={<IncidentDetailPage />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/scenarios" element={<ScenarioPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/datasources" element={<DataSourcesPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/search" element={<SearchPage />} />
      </Route>
    </Routes>
  );
}
