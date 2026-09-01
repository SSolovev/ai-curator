#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
test -f models/Qwen3-4B-Q4_K_M.gguf || {
  echo "Сначала выполните ./scripts/prepare-model.sh"
  exit 1
}
test -s secrets/hmac_key || { echo "Сначала выполните ./scripts/prepare-model.sh"; exit 1; }
docker compose up --build -d
echo "ИИ-Куратор запускается. Откройте http://127.0.0.1:${CURATOR_PORT:-8080}"

