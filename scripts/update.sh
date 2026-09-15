#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
docker compose --profile llama pull model
docker compose build --pull app
./scripts/start.sh
echo "Обновление применено. Проверка: http://127.0.0.1:${CURATOR_PORT:-8080}/healthz"
