#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

dotenv_value() {
  key=$1
  [ -f .env ] || return 0
  sed -n "s/^${key}=//p" .env | tail -n 1 | sed "s/^['\"]//; s/['\"]$//"
}

stop_ollama_model() {
  command -v ollama >/dev/null 2>&1 || return 0
  ollama stop "$1" >/dev/null 2>&1 || true
}

free_curator_ollama_models() {
  stop_ollama_model "$quality_model"
  stop_ollama_model ai-curator-qwen3-4b
  stop_ollama_model ai-curator-qwen3-8b
  stop_ollama_model ai-curator-gemma4-12b
  stop_ollama_model ai-curator-pii-gemma3-4b
}

runtime=${CURATOR_LLM_RUNTIME:-$(dotenv_value CURATOR_LLM_RUNTIME)}
runtime=${runtime:-llama_cpp}
quality_model=${CURATOR_OLLAMA_MODEL:-$(dotenv_value CURATOR_OLLAMA_MODEL)}
quality_model=${quality_model:-ai-curator-gemma4-12b}
pii_model=${CURATOR_PII_FILTER_MODEL:-$(dotenv_value CURATOR_PII_FILTER_MODEL)}
pii_model=${pii_model:-ai-curator-pii-gemma3-4b}
external_enabled=${CURATOR_EXTERNAL_LLM_ENABLED:-$(dotenv_value CURATOR_EXTERNAL_LLM_ENABLED)}
external_enabled=${external_enabled:-false}
external_key_file=${CURATOR_EXTERNAL_LLM_API_KEY_FILE:-$(dotenv_value CURATOR_EXTERNAL_LLM_API_KEY_FILE)}
external_key_file=${external_key_file:-/secrets/external_llm_api_key}
pii_dictionary_file=${CURATOR_PII_FILTER_DICTIONARY_FILE:-$(dotenv_value CURATOR_PII_FILTER_DICTIONARY_FILE)}
pii_dictionary_file=${pii_dictionary_file:-/secrets/pii_dictionary.txt}

test -s secrets/hmac_key || { echo "Сначала выполните ./scripts/prepare-model.sh"; exit 1; }

# Every main runtime is behind the same local, fail-closed privacy boundary.
command -v ollama >/dev/null 2>&1 || {
  echo "Ollama не установлен: он нужен для обязательного PII-фильтра."
  exit 1
}
ollama show "$pii_model" >/dev/null 2>&1 || {
  echo "PII-модель $pii_model не найдена. Выполните ./scripts/prepare-ollama.sh pii-filter"
  exit 1
}
case "$pii_dictionary_file" in
  /secrets/*..*) echo "Путь PII-словаря не должен содержать '..'."; exit 1 ;;
  /secrets/*) host_pii_dictionary="secrets/${pii_dictionary_file#/secrets/}" ;;
  *) echo "Путь PII-словаря должен находиться внутри /secrets/."; exit 1 ;;
esac
test -s "$host_pii_dictionary" || {
  echo "Создайте непустой $host_pii_dictionary: он обязателен для любого runtime."
  exit 1
}

case "$runtime" in
  llama_cpp)
    test -f models/Qwen3-4B-Q4_K_M.gguf || {
      echo "Сначала выполните ./scripts/prepare-model.sh"
      exit 1
    }
    docker compose stop app >/dev/null 2>&1 || true
    free_curator_ollama_models
    docker compose --profile llama --profile demo up --build -d app model mock-jira
    ;;
  ollama)
    command -v ollama >/dev/null 2>&1 || {
      echo "Ollama не установлен. Установите Native Ollama и выполните ./scripts/prepare-ollama.sh"
      exit 1
    }
    ollama show "$quality_model" >/dev/null 2>&1 || {
      echo "Модель $quality_model не найдена. Выполните ./scripts/prepare-ollama.sh"
      exit 1
    }
    docker compose stop app >/dev/null 2>&1 || true
    docker compose --profile llama stop model >/dev/null 2>&1 || true
    free_curator_ollama_models
    docker compose --profile demo up --build -d --no-deps app mock-jira
    ;;
  external)
    case "$external_enabled" in
      true|True|TRUE|1|yes|Yes|YES) ;;
      *)
        echo "Для external установите CURATOR_EXTERNAL_LLM_ENABLED=true."
        exit 1
        ;;
    esac
    case "$external_key_file" in
      /secrets/*..*) echo "Путь API-ключа не должен содержать '..'."; exit 1 ;;
      /secrets/*) host_external_key="secrets/${external_key_file#/secrets/}" ;;
      *) echo "Путь API-ключа должен находиться внутри /secrets/."; exit 1 ;;
    esac
    test -s "$host_external_key" || {
      echo "Создайте $host_external_key с API-ключом внешней модели."
      exit 1
    }
    docker compose stop app >/dev/null 2>&1 || true
    docker compose --profile llama stop model >/dev/null 2>&1 || true
    free_curator_ollama_models
    docker compose --profile demo up --build -d --no-deps app mock-jira
    ;;
  *)
    echo "Неизвестный CURATOR_LLM_RUNTIME=$runtime. Допустимо: llama_cpp, ollama или external"
    exit 1
    ;;
esac

echo "ИИ-Куратор запускается с runtime=$runtime. Откройте http://127.0.0.1:${CURATOR_PORT:-8080}"
