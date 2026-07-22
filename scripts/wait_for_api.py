from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/health/ready"
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.load(response)
            if data.get("status") == "ready":
                print(f"API ready: {url}")
                raise SystemExit(0)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        time.sleep(1)
print(f"API did not become ready: {url}", file=sys.stderr)
raise SystemExit(1)
