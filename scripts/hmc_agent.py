#!/usr/bin/env python3
"""Small Linux-oriented collector that posts host metrics to HMC.

Run on a future real server:

    HMC_URL=https://observability.example.com HMC_API_TOKEN=hmc_... HMC_TENANT_ID=... HMC_HOST_ID=... python hmc_agent.py
"""

import json
import os
import time
import urllib.request


def read_cpu():
    with open("/proc/stat", encoding="utf-8") as handle:
        parts = handle.readline().split()
    idle, total = int(parts[4]), sum(int(part) for part in parts[1:])
    time.sleep(0.2)
    with open("/proc/stat", encoding="utf-8") as handle:
        parts = handle.readline().split()
    idle2, total2 = int(parts[4]), sum(int(part) for part in parts[1:])
    delta = max(1, total2 - total)
    return round((1 - ((idle2 - idle) / delta)) * 100, 2)


def main():
    base = os.environ["HMC_URL"].rstrip("/")
    token = os.environ["HMC_API_TOKEN"]
    tenant = os.environ["HMC_TENANT_ID"]
    host = os.environ["HMC_HOST_ID"]
    payload = {
        "items": [
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tenant_id": tenant,
                "host_id": host,
                "metric_name": "cpu_usage_percent",
                "value": read_cpu() if os.path.exists("/proc/stat") else 0,
                "unit": "percent",
                "source_type": "real",
                "labels": {"collector": "hmc-agent"},
            }
        ]
    }
    request = urllib.request.Request(
        f"{base}/api/v1/ingest/metrics",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        print(response.status, response.read().decode())


if __name__ == "__main__":
    main()
