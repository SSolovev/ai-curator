#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
docker compose stop app model >/dev/null 2>&1 || true
docker rm -f ai-curator-model-benchmark >/dev/null 2>&1 || true
started_at=$(date +%s)
docker compose run -d --name ai-curator-model-benchmark -p 127.0.0.1:18081:8080 model >/dev/null
cleanup() { docker rm -f ai-curator-model-benchmark >/dev/null 2>&1 || true; docker compose up -d >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
.venv/bin/python scripts/benchmark_model.py --url http://127.0.0.1:18081 --container ai-curator-model-benchmark
