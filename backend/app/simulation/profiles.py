from __future__ import annotations

PROFILES = {
    "quick": {
        "tenants": 8,
        "hosts": 50,
        "logs": 100_000,
        "metrics": 300_000,
        "days": 7,
        "batch_size": 2000,
    },
    "standard": {
        "tenants": 25,
        "hosts": 250,
        "logs": 750_000,
        "metrics": 3_000_000,
        "days": 14,
        "batch_size": 5000,
    },
    "large": {
        "tenants": 60,
        "hosts": 600,
        "logs": 2_000_000,
        "metrics": 10_000_000,
        "days": 30,
        "batch_size": 5000,
    },
    "free": {
        "tenants": 8,
        "hosts": 16,
        "logs": 2_000,
        "metrics": 20_000,
        "days": 1,
        "batch_size": 1000,
    },
    "noc": {
        "tenants": 100,
        "hosts": 400,
        "logs": 80_000,
        "metrics": 600_000,
        "days": 1,
        "batch_size": 5000,
    },
}


def estimate_profile(name: str) -> dict:
    profile = PROFILES[name]
    metric_bytes = profile["metrics"] * 180
    log_bytes = profile["logs"] * 420
    disk_mb = int((metric_bytes + log_bytes) / (1024 * 1024)) + 256
    ram_mb = 384 + int(profile["hosts"] * 0.8) + int(profile["batch_size"] / 10)
    return {
        "name": name,
        **profile,
        "estimated_disk_mb": disk_mb,
        "estimated_ram_mb": ram_mb,
        "recommended_free_ram_mb": ram_mb + 512,
        "recommended_free_disk_mb": disk_mb + 1024,
        "note": "Seed writes in batches and does not load the full history into memory.",
    }
