#!/usr/bin/env sh
set -eu

base_url="${RESTORATION_URL:-http://localhost:8080}"
python - "$base_url" <<'PY'
import json
import sys
import urllib.request

base_url = sys.argv[1]
with urllib.request.urlopen(f"{base_url}/api/health", timeout=10) as response:
    health = json.load(response)

unavailable = [
    name
    for name, state in health["backends"].items()
    if state["status"] != "ok"
]
if unavailable:
    raise SystemExit(f"Unavailable GPU services: {', '.join(unavailable)}")
print("Gateway and both GPU backends are healthy.")
PY
