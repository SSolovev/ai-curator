#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
container_name="ai-curator-smoke-test"
cd "$project_dir"
docker build -t ai-curator-app:smoke .
docker run -d --name "$container_name" -p 127.0.0.1:18080:8080 \
  --read-only --tmpfs /tmp \
  --mount "type=bind,source=$project_dir/docs/pii_dictionary.example.txt,target=/secrets/pii_dictionary.txt,readonly" \
  ai-curator-app:smoke >/dev/null
cleanup() { docker rm -f "$container_name" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
attempt=0
while [ "$attempt" -lt 30 ]; do
  if curl -fsS http://127.0.0.1:18080/healthz | grep -q '"ok":true'; then echo "Container smoke-test: PASS"; exit 0; fi
  attempt=$((attempt + 1)); sleep 1
done
docker logs "$container_name" || true
echo "Container smoke-test: FAIL" >&2
exit 1
