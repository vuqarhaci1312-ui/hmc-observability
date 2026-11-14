"""Send one real sample through the ingestion API and read it back."""

import json
import os
import time
import urllib.request
from uuid import uuid4


def request(method, url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode()
        return response.status, json.loads(body) if body else None


def main():
    base = os.environ.get("HMC_URL", "http://127.0.0.1:8080").rstrip("/")
    email = os.environ["DEMO_ADMIN_EMAIL"]
    password = os.environ["DEMO_ADMIN_PASSWORD"]
    status, login = request("POST", f"{base}/api/v1/auth/login", "", {"email": email, "password": password})
    token = login["access_token"]
    tenant = os.environ.get("HMC_TENANT_ID")
    host = os.environ.get("HMC_HOST_ID") or str(uuid4())
    if not tenant:
        _status, tenants = request("GET", f"{base}/api/v1/tenants?mode=demo", token)
        tenant = tenants["items"][0]["id"]
    status, queued = request(
        "POST",
        f"{base}/api/v1/ingest/metrics",
        token,
        {"items": [{"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tenant_id": tenant, "host_id": host, "metric_name": "cpu_usage_percent", "value": 12.5, "unit": "percent", "source_type": "real", "labels": {"collector": "smoke"}}]},
    )
    print({"ingest_status": status, "queued": queued})


if __name__ == "__main__":
    main()
