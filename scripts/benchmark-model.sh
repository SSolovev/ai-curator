#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
docker compose --profile llama stop app model >/dev/null 2>&1 || true
if command -v ollama >/dev/null 2>&1; then
  ollama stop ai-curator-qwen3-4b >/dev/null 2>&1 || true
  ollama stop ai-curator-qwen3-8b >/dev/null 2>&1 || true
  ollama stop ai-curator-gemma4-12b >/dev/null 2>&1 || true
  ollama stop ai-curator-pii-gemma3-4b >/dev/null 2>&1 || true
fi
docker rm -f ai-curator-model-benchmark >/dev/null 2>&1 || true
started_at=$(date +%s)
docker compose --profile llama run -d --name ai-curator-model-benchmark -p 127.0.0.1:18081:8080 model >/dev/null
cleanup() { docker rm -f ai-curator-model-benchmark >/dev/null 2>&1 || true; ./scripts/start.sh >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
.venv/bin/python scripts/benchmark_model.py --url http://127.0.0.1:18081 --container ai-curator-model-benchmark
