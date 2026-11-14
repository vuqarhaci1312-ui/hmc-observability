from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid5

from app.simulation.formulas import HostProfile

ARCHETYPES = [
    ("Şimal Hosting MMC", "hosting", ["web", "db", "mail", "storage"]),
    ("Qızıl Bazar", "ecommerce", ["web", "app", "db", "app"]),
    ("Xəzər Restoran Şəbəkəsi", "restaurant", ["web", "pbx", "mail"]),
    ("Körpü Korporativ Ofis", "office", ["windows", "mail", "pbx"]),
    ("Danışıq Mərkəzi", "callcenter", ["pbx", "pbx", "app", "windows"]),
    ("Araz Maliyyə", "finance", ["app", "db", "db", "web"]),
    ("Kodxətt Proqram", "software", ["app", "app", "web", "db"]),
    ("Yolçuluq Logistika", "logistics", ["app", "web", "db", "storage"]),
    ("Nura Sağlamlıq", "healthcare", ["app", "db", "storage", "web"]),
    ("Məktub Poçt Hosting", "mailhost", ["mail", "mail", "storage", "db"]),
]

REGIONS = [
    ("baku", "Bakı", "Asia/Baku"),
    ("frankfurt", "Frankfurt", "Europe/Berlin"),
    ("istanbul", "İstanbul", "Europe/Istanbul"),
]

OS_BY_ROLE = {
    "windows": "Windows Server 2022",
    "pbx": "Debian 12 / Asterisk",
    "mail": "Ubuntu 24.04",
    "db": "Rocky Linux 9",
    "hypervisor": "Proxmox VE 8",
    "storage": "Ubuntu 24.04",
    "network": "SONiC",
    "web": "Ubuntu 24.04",
    "app": "Ubuntu 24.04",
}


def stable_uuid(kind: str, *parts: object) -> UUID:
    return uuid5(UUID("6f1d3c2a-8b4e-4d6a-9c11-a1b2c3d4e5f6"), ":".join([kind, *map(str, parts)]))


def ip_for(index: int) -> str:
    return f"10.{(index // 65025) % 256}.{(index // 255) % 256}.{(index % 254) + 1}"


def mac_for(index: int) -> str:
    value = 0x020000000000 + (index % 0xFFFFFFFFFF)
    raw = value.to_bytes(6, "big")
    return ":".join(f"{byte:02x}" for byte in raw)


@dataclass
class WorldHost:
    id: UUID
    tenant_id: UUID | None
    cluster_id: UUID
    hostname: str
    host_kind: str
    role: str
    os_name: str
    ip_address: str
    environment: str
    cpu_cores: int
    memory_bytes: int
    disk_bytes: int
    physical_host_id: UUID | None
    profile: HostProfile
    region_code: str
    datacenter_id: UUID


@dataclass
class World:
    regions: list[dict] = field(default_factory=list)
    datacenters: list[dict] = field(default_factory=list)
    clusters: list[dict] = field(default_factory=list)
    tenants: list[dict] = field(default_factory=list)
    environments: list[dict] = field(default_factory=list)
    hosts: list[WorldHost] = field(default_factory=list)
    vms: list[dict] = field(default_factory=list)
    interfaces: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)


def _weights(count: int) -> list[int]:
    pattern = [1, 2, 4, 7, 3, 5, 2, 8, 6, 3, 9, 4]
    return [pattern[index % len(pattern)] + (index % 4) for index in range(count)]


def _capacity(role: str, index: int) -> tuple[int, int, int]:
    if role == "hypervisor":
        return 64, 512 * 1024**3, 8 * 1024**4
    if role == "db":
        cores = 8 + (index % 4) * 4
        return cores, cores * 8 * 1024**3, 2 * 1024**4
    if role in {"mail", "storage"}:
        return 8, 32 * 1024**3, 4 * 1024**4
    if role == "pbx":
        return 4, 8 * 1024**3, 200 * 1024**3
    cores = 2 + (index % 5) * 2
    return cores, cores * 4 * 1024**3, 400 * 1024**3


def build_world(seed: int, tenant_count: int, host_count: int) -> World:
    world = World()
    for code, name, timezone in REGIONS:
        region_id = stable_uuid("region", code)
        world.regions.append({"id": region_id, "code": code, "name": name, "timezone": timezone})
        datacenter_id = stable_uuid("dc", code)
        world.datacenters.append(
            {
                "id": datacenter_id,
                "region_id": region_id,
                "name": f"HMC {name} DC1",
                "code": f"{code}-dc1",
            }
        )
        world.relationships.append(
            {
                "tenant_id": None,
                "from_type": "region",
                "from_id": region_id,
                "to_type": "datacenter",
                "to_id": datacenter_id,
                "relationship": "contains",
            }
        )
        for cluster_index in (1, 2):
            cluster_id = stable_uuid("cluster", code, cluster_index)
            world.clusters.append(
                {
                    "id": cluster_id,
                    "datacenter_id": datacenter_id,
                    "name": f"{code}-cl{cluster_index}",
                    "kind": "virtualization" if cluster_index == 1 else "compute",
                }
            )
            world.relationships.append(
                {
                    "tenant_id": None,
                    "from_type": "datacenter",
                    "from_id": datacenter_id,
                    "to_type": "cluster",
                    "to_id": cluster_id,
                    "relationship": "contains",
                }
            )

    reserved = 5 if host_count >= 12 else 0
    allocatable = host_count - reserved
    weights = _weights(tenant_count)
    scale = allocatable / sum(weights)
    counts = [max(1, int(weight * scale)) for weight in weights]
    while sum(counts) > allocatable:
        counts[counts.index(max(counts))] -= 1
    while sum(counts) < allocatable:
        counts[counts.index(min(counts))] += 1

    for index in range(tenant_count):
        base_name, profile, _roles = ARCHETYPES[index % len(ARCHETYPES)]
        name = base_name if index < len(ARCHETYPES) else f"{base_name} {index + 1}"
        tenant_id = stable_uuid("tenant", seed, index)
        slug = f"tenant-{index + 1:02d}"
        environment = "production" if index % 5 else "staging"
        world.tenants.append(
            {
                "id": tenant_id,
                "name": name,
                "slug": slug,
                "profile": profile,
                "environment": environment,
                "source_type": "demo",
                "sla_target": 99.5 + (index % 5) * 0.1,
            }
        )
        env_id = stable_uuid("env", tenant_id)
        world.environments.append({"id": env_id, "tenant_id": tenant_id, "name": environment, "kind": environment})

    host_index = 0
    for tenant_index, count in enumerate(counts):
        tenant = world.tenants[tenant_index]
        _name, profile_name, roles = ARCHETYPES[tenant_index % len(ARCHETYPES)]
        for offset in range(count):
            role = roles[offset % len(roles)]
            region = world.regions[(tenant_index + offset) % len(world.regions)]
            datacenter = next(item for item in world.datacenters if item["region_id"] == region["id"])
            cluster = [item for item in world.clusters if item["datacenter_id"] == datacenter["id"]][offset % 2]
            cores, memory, disk = _capacity(role, host_index)
            host_id = stable_uuid("host", seed, host_index)
            hostname = f"{tenant['slug']}-{role}-{offset + 1:02d}.demo.hmc.local"
            profile = HostProfile(
                key=hostname,
                role=role,
                cpu_cores=cores,
                memory_bytes=memory,
                disk_bytes=disk,
                cpu_idle=0.08 + ((seed + host_index) % 17) / 100,
                memory_working_set=0.28 + ((seed + host_index) % 23) / 100,
                disk_used_ratio=0.22 + ((seed + host_index) % 31) / 100,
                business_weight=0.45 + ((tenant_index + offset) % 6) / 10,
                workload=profile_name,
            )
            host = WorldHost(
                id=host_id,
                tenant_id=tenant["id"],
                cluster_id=cluster["id"],
                hostname=hostname,
                host_kind="physical" if role in {"hypervisor", "storage"} or offset == 0 else "virtual",
                role=role,
                os_name=OS_BY_ROLE[role],
                ip_address=ip_for(host_index + 1),
                environment=tenant["environment"],
                cpu_cores=cores,
                memory_bytes=memory,
                disk_bytes=disk,
                physical_host_id=None,
                profile=profile,
                region_code=region["code"],
                datacenter_id=datacenter["id"],
            )
            world.hosts.append(host)
            world.interfaces.append(
                {
                    "id": stable_uuid("nic", host_id),
                    "host_id": host_id,
                    "name": "eth0",
                    "mac_address": mac_for(host_index + 1),
                    "ip_address": host.ip_address,
                    "speed_mbps": 10000 if role in {"hypervisor", "storage", "db"} else 1000,
                }
            )
            _add_services(world, host)
            world.relationships.append(
                {
                    "tenant_id": tenant["id"],
                    "from_type": "cluster",
                    "from_id": cluster["id"],
                    "to_type": "host",
                    "to_id": host_id,
                    "relationship": "contains",
                }
            )
            host_index += 1

    if reserved:
        _add_shared_hypervisor(world, seed, host_index)
    return world


def _add_services(world: World, host: WorldHost) -> None:
    catalog = {
        "web": [("nginx", "nginx", "web", 443)],
        "app": [("app", "application", "app", 8080)],
        "db": [("postgres", "postgresql", "db", 5432)],
        "mail": [("postfix", "postfix", "mail", 25), ("dovecot", "dovecot", "mail", 993)],
        "pbx": [("asterisk", "pbx", "pbx", 5060)],
        "storage": [("storage", "storage", "storage", 3260)],
        "windows": [("iis", "windows", "app", 443)],
        "hypervisor": [("libvirt", "cloud", "hypervisor", 8006)],
        "network": [("edge", "network", "network", None)],
    }
    created = []
    for name, component, kind, port in catalog.get(host.role, catalog["app"]):
        service_id = stable_uuid("service", host.id, name)
        world.services.append(
            {
                "id": service_id,
                "tenant_id": host.tenant_id,
                "host_id": host.id,
                "name": name,
                "kind": kind,
                "component": component,
                "environment": host.environment,
                "source_type": "demo",
                "health_status": "unknown",
                "port": port,
            }
        )
        world.services_instances = getattr(world, "services_instances", [])
        created.append(service_id)
        world.relationships.append(
            {
                "tenant_id": host.tenant_id,
                "from_type": "host",
                "from_id": host.id,
                "to_type": "service",
                "to_id": service_id,
                "relationship": "runs",
            }
        )
    if host.role == "app":
        database = next((item for item in world.services if item["kind"] == "db" and item["tenant_id"] == host.tenant_id), None)
        if database:
            world.dependencies.append(
                {
                    "id": stable_uuid("dep", created[0], database["id"]),
                    "service_id": created[0],
                    "depends_on_service_id": database["id"],
                    "kind": "depends_on",
                }
            )


def _add_shared_hypervisor(world: World, seed: int, start_index: int) -> None:
    region = world.regions[0]
    datacenter = world.datacenters[0]
    cluster = world.clusters[0]
    host_id = stable_uuid("host", seed, "shared-hv")
    profile = HostProfile(
        key="hmc-hv-shared-01.demo.hmc.local",
        role="hypervisor",
        cpu_cores=64,
        memory_bytes=512 * 1024**3,
        disk_bytes=8 * 1024**4,
        cpu_idle=0.18,
        memory_working_set=0.46,
        disk_used_ratio=0.41,
        business_weight=0.7,
        workload="hypervisor",
    )
    physical = WorldHost(
        id=host_id,
        tenant_id=None,
        cluster_id=cluster["id"],
        hostname="hmc-hv-shared-01.demo.hmc.local",
        host_kind="physical",
        role="hypervisor",
        os_name=OS_BY_ROLE["hypervisor"],
        ip_address=ip_for(start_index + 1),
        environment="production",
        cpu_cores=64,
        memory_bytes=512 * 1024**3,
        disk_bytes=8 * 1024**4,
        physical_host_id=None,
        profile=profile,
        region_code=region["code"],
        datacenter_id=datacenter["id"],
    )
    world.hosts.append(physical)
    world.interfaces.append(
        {
            "id": stable_uuid("nic", host_id),
            "host_id": host_id,
            "name": "bond0",
            "mac_address": mac_for(900000),
            "ip_address": physical.ip_address,
            "speed_mbps": 25000,
        }
    )
    _add_services(world, physical)
    vm_roles = ["app", "db", "mail", "web"]
    for offset, role in enumerate(vm_roles):
        tenant = world.tenants[offset % len(world.tenants)]
        vm_host_id = stable_uuid("host", seed, "shared-vm", offset)
        cores, memory, disk = _capacity(role, offset)
        vm_profile = HostProfile(
            key=f"shared-{role}-{offset + 1}.demo.hmc.local",
            role=role,
            cpu_cores=cores,
            memory_bytes=memory,
            disk_bytes=disk,
            cpu_idle=0.12 + offset / 100,
            memory_working_set=0.35 + offset / 50,
            disk_used_ratio=0.33 + offset / 80,
            business_weight=0.8,
            workload="shared",
        )
        vm_host = WorldHost(
            id=vm_host_id,
            tenant_id=tenant["id"],
            cluster_id=cluster["id"],
            hostname=f"{tenant['slug']}-shared-{role}.demo.hmc.local",
            host_kind="virtual",
            role=role,
            os_name=OS_BY_ROLE[role],
            ip_address=ip_for(start_index + 2 + offset),
            environment=tenant["environment"],
            cpu_cores=cores,
            memory_bytes=memory,
            disk_bytes=disk,
            physical_host_id=host_id,
            profile=vm_profile,
            region_code=region["code"],
            datacenter_id=datacenter["id"],
        )
        world.hosts.append(vm_host)
        world.vms.append(
            {
                "id": stable_uuid("vm", vm_host_id),
                "host_id": vm_host_id,
                "physical_host_id": host_id,
                "tenant_id": tenant["id"],
                "name": vm_host.hostname,
                "vcpu": cores,
                "memory_bytes": memory,
                "source_type": "demo",
            }
        )
        world.relationships.append(
            {
                "tenant_id": tenant["id"],
                "from_type": "host",
                "from_id": host_id,
                "to_type": "virtual_machine",
                "to_id": vm_host_id,
                "relationship": "runs",
            }
        )
        _add_services(world, vm_host)
