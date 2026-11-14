from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


def ts_now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = ts_now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UserTenantMembership(Base):
    __tablename__ = "user_tenant_memberships"
    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = ts_now()


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = ts_now()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    profile: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), default="production", nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), default="demo", nullable=False)
    sla_target: Mapped[float] = mapped_column(Float, default=99.9, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class Environment(Base):
    __tablename__ = "environments"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)


class Region(Base):
    __tablename__ = "regions"
    id: Mapped[UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Baku", nullable=False)


class Datacenter(Base):
    __tablename__ = "datacenters"
    id: Mapped[UUID] = uuid_pk()
    region_id: Mapped[UUID] = mapped_column(ForeignKey("regions.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)


class Cluster(Base):
    __tablename__ = "clusters"
    id: Mapped[UUID] = uuid_pk()
    datacenter_id: Mapped[UUID] = mapped_column(ForeignKey("datacenters.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)


class Host(Base):
    __tablename__ = "hosts"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    cluster_id: Mapped[UUID | None] = mapped_column(ForeignKey("clusters.id", ondelete="SET NULL"), index=True)
    hostname: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    host_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    os_name: Mapped[str] = mapped_column(String(120), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disk_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    health_status: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)
    uptime_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    physical_host_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    baseline: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    seed_id: Mapped[str | None] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = ts_now()


class VirtualMachine(Base):
    __tablename__ = "virtual_machines"
    id: Mapped[UUID] = uuid_pk()
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), unique=True, nullable=False)
    physical_host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)


class HostInterface(Base):
    __tablename__ = "host_interfaces"
    id: Mapped[UUID] = uuid_pk()
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    mac_address: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    speed_mbps: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)


class Service(Base):
    __tablename__ = "services"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    component: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    health_status: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)
    port: Mapped[int | None] = mapped_column(Integer)


class ServiceInstance(Base):
    __tablename__ = "service_instances"
    id: Mapped[UUID] = uuid_pk()
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True, nullable=False)
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True, nullable=False)
    instance_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running", nullable=False)


class ServiceDependency(Base):
    __tablename__ = "service_dependencies"
    id: Mapped[UUID] = uuid_pk()
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True, nullable=False)
    depends_on_service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="depends_on", nullable=False)


class InfrastructureRelationship(Base):
    __tablename__ = "infrastructure_relationships"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    from_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    to_type: Mapped[str] = mapped_column(String(40), nullable=False)
    to_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    relationship: Mapped[str] = mapped_column(String(40), nullable=False)


class TelemetrySource(Base):
    __tablename__ = "telemetry_sources"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    host_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class MetricSample(Base):
    __tablename__ = "metric_samples"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    host_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    service_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, default=NIL_UUID)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scenario_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    labels: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    quality: Mapped[str] = mapped_column(String(20), default="good", nullable=False)
    seed_id: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (
        UniqueConstraint("tenant_id", "host_id", "service_id", "metric_name", "timestamp", name="uq_metric_point"),
        Index("ix_ms_tenant_ts", "tenant_id", "timestamp"),
        Index("ix_ms_host_ts", "host_id", "timestamp"),
        Index("ix_ms_metric_ts", "metric_name", "timestamp"),
        Index("ix_ms_scope_ts", "tenant_id", "host_id", "metric_name", "timestamp"),
    )


class MetricAggregate(Base):
    __tablename__ = "metric_aggregates"
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    host_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    bucket_size: Mapped[str] = mapped_column(String(16), primary_key=True)
    avg_value: Mapped[float] = mapped_column(Float, nullable=False)
    min_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    id: Mapped[UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = ts_now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class IngestionError(Base):
    __tablename__ = "ingestion_errors"
    id: Mapped[UUID] = uuid_pk()
    batch_id: Mapped[UUID | None] = mapped_column(ForeignKey("ingestion_batches.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class SourceHealth(Base):
    __tablename__ = "source_health"
    id: Mapped[UUID] = uuid_pk()
    source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    host_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lag_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    detail: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_name: Mapped[str | None] = mapped_column(String(80))
    comparator: Mapped[str] = mapped_column(String(8), default="gt", nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)
    for_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    resolve_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    hysteresis: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    min_volume: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expression: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class AlertRuleVersion(Base):
    __tablename__ = "alert_rule_versions"
    id: Mapped[UUID] = uuid_pk()
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class AlertEvaluation(Base):
    __tablename__ = "alert_evaluations"
    id: Mapped[UUID] = uuid_pk()
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True, nullable=False)
    host_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    created_at: Mapped[datetime] = ts_now()


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[UUID] = uuid_pk()
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True, nullable=False)
    host_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True, nullable=False)
    service_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    dedup_key: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    correlation_key: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    last_value: Mapped[float | None] = mapped_column(Float)
    scenario_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    firing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    host_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = ts_now()


class Silence(Base):
    __tablename__ = "silences"
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    matchers: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class NotificationRecord(Base):
    __tablename__ = "notification_records"
    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = ts_now()
    link: Mapped[str | None] = mapped_column(String(300))


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="p3", nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True, nullable=False)
    affected_tenant_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    affected_host_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    affected_service_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    hypothesis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cause_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remediation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    correlation_key: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    scenario_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_type: Mapped[str] = mapped_column(String(16), default="demo", nullable=False)
    created_at: Mapped[datetime] = ts_now()


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    id: Mapped[UUID] = uuid_pk()
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), default="system", nullable=False)
    created_at: Mapped[datetime] = ts_now()
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class IncidentAssignment(Base):
    __tablename__ = "incident_assignments"
    id: Mapped[UUID] = uuid_pk()
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = ts_now()


class IncidentComment(Base):
    __tablename__ = "incident_comments"
    id: Mapped[UUID] = uuid_pk()
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class IncidentEvidence(Base):
    __tablename__ = "incident_evidence"
    id: Mapped[UUID] = uuid_pk()
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class IncidentRelationship(Base):
    __tablename__ = "incident_relationships"
    id: Mapped[UUID] = uuid_pk()
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False)
    related_type: Mapped[str] = mapped_column(String(40), nullable=False)
    related_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    relationship: Mapped[str] = mapped_column(String(40), nullable=False)


class IncidentTicket(Base):
    __tablename__ = "incident_tickets"
    id: Mapped[UUID] = uuid_pk()
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="local", nullable=False)
    external_key: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = ts_now()


class ScenarioDefinition(Base):
    __tablename__ = "scenario_definitions"
    id: Mapped[UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    infrastructure_type: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_alerts: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expected_incidents: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recovery: Mapped[str] = mapped_column(Text, default="", nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ScenarioRun(Base):
    __tablename__ = "scenario_runs"
    id: Mapped[UUID] = uuid_pk()
    definition_id: Mapped[UUID] = mapped_column(ForeignKey("scenario_definitions.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    phase: Mapped[str] = mapped_column(String(40), default="healthy", nullable=False)
    speed: Mapped[float] = mapped_column(Float, default=1, nullable=False)
    intensity: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    host_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    service_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    virtual_elapsed_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    logs_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_triggered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    incidents_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evaluation: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ScenarioEvent(Base):
    __tablename__ = "scenario_events"
    id: Mapped[UUID] = uuid_pk()
    run_id: Mapped[UUID] = mapped_column(ForeignKey("scenario_runs.id", ondelete="CASCADE"), index=True, nullable=False)
    phase: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class SimulationProfile(Base):
    __tablename__ = "simulation_profiles"
    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    tenant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    host_count: Mapped[int] = mapped_column(Integer, nullable=False)
    log_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_count: Mapped[int] = mapped_column(Integer, nullable=False)
    history_days: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_disk_mb: Mapped[int] = mapped_column(Integer, nullable=False)


class SimulationJob(Base):
    __tablename__ = "simulation_jobs"
    id: Mapped[UUID] = uuid_pk()
    profile_name: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    seed_id: Mapped[str] = mapped_column(String(40), nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    metrics_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    logs_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    counters: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = ts_now()


class SimulationCheckpoint(Base):
    __tablename__ = "simulation_checkpoints"
    id: Mapped[UUID] = uuid_pk()
    job_id: Mapped[UUID] = mapped_column(ForeignKey("simulation_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    host_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True, nullable=False)
    counters: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = ts_now()
    __table_args__ = (UniqueConstraint("job_id", "host_id", name="uq_checkpoint"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = uuid_pk()
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(80))
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = ts_now()


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = ts_now()


class IntegrationSetting(Base):
    __tablename__ = "integration_settings"
    id: Mapped[UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    message: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class DataRetentionPolicy(Base):
    __tablename__ = "data_retention_policies"
    id: Mapped[UUID] = uuid_pk()
    data_class: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    retain_days: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
