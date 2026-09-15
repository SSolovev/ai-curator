#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

dotenv_value() {
  key=$1
  [ -f .env ] || return 0
  sed -n "s/^${key}=//p" .env | tail -n 1 | sed "s/^['\"]//; s/['\"]$//"
}

quality_model=${CURATOR_OLLAMA_MODEL:-$(dotenv_value CURATOR_OLLAMA_MODEL)}
quality_model=${quality_model:-ai-curator-gemma4-12b}

docker compose --profile llama --profile demo down
if command -v ollama >/dev/null 2>&1; then
  ollama stop "$quality_model" >/dev/null 2>&1 || true
  ollama stop ai-curator-qwen3-4b >/dev/null 2>&1 || true
  ollama stop ai-curator-qwen3-8b >/dev/null 2>&1 || true
  ollama stop ai-curator-gemma4-12b >/dev/null 2>&1 || true
  ollama stop ai-curator-pii-gemma3-4b >/dev/null 2>&1 || true
fi
