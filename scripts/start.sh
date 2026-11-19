#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

get_setting() {
  name=$1
  default=$2
  value=$(printenv "$name" || true)
  if [ -z "$value" ] && [ -f .env ]; then
    value=$(sed -n "s/^${name}=//p" .env | tail -n 1 | sed "s/^['\"]//; s/['\"]$//")
  fi
  printf '%s\n' "${value:-$default}"
}

require_secret_file() {
  case "$1" in
    /secrets/*..* | /secrets/ | /secrets/*/) fail "Ожидается путь к файлу внутри /secrets/." ;;
    /secrets/*) local_path="secrets/${1#/secrets/}" ;;
    *) fail "Ожидается путь к файлу внутри /secrets/." ;;
  esac
  [ -f "$local_path" ] && [ -s "$local_path" ] || fail "Создайте непустой $local_path."
}

runtime=$(get_setting CURATOR_LLM_RUNTIME llama_cpp)
port=$(get_setting CURATOR_PORT 8080)
main_model=$(get_setting CURATOR_OLLAMA_MODEL ai-curator-gemma4-12b)
pii_model=$(get_setting CURATOR_PII_FILTER_MODEL ai-curator-pii-gemma3-4b)
pii_dictionary=$(get_setting CURATOR_PII_FILTER_DICTIONARY_FILE /secrets/pii_dictionary.txt)

[ -s secrets/hmac_key ] || fail "Сначала подготовьте secrets/hmac_key по инструкции установки."
command -v ollama >/dev/null 2>&1 || fail "Установите Ollama для обязательного PII-фильтра."
ollama show "$pii_model" >/dev/null 2>&1 ||
  fail "PII-модель $pii_model не найдена. Выполните ./scripts/prepare-ollama.sh pii-filter"
require_secret_file "$pii_dictionary"

case "$runtime" in
  llama_cpp)
    [ -f models/Qwen3-4B-Q4_K_M.gguf ] || fail "Сначала выполните ./scripts/prepare-model.sh"
    ;;
  ollama)
    ollama show "$main_model" >/dev/null 2>&1 ||
      fail "Модель $main_model не найдена. Выполните ./scripts/prepare-ollama.sh"
    ;;
  external)
    case "$(get_setting CURATOR_EXTERNAL_LLM_ENABLED false)" in
      true|True|TRUE|1|yes|Yes|YES) ;;
      *) fail "Для external установите CURATOR_EXTERNAL_LLM_ENABLED=true." ;;
    esac
    require_secret_file "$(get_setting CURATOR_EXTERNAL_LLM_API_KEY_FILE /secrets/external_llm_api_key)"
    ;;
  *) fail "Неизвестный runtime $runtime. Допустимо: llama_cpp, ollama или external." ;;
esac

docker compose stop app >/dev/null 2>&1 || true
if [ "$runtime" != llama_cpp ]; then
  docker compose --profile llama stop model >/dev/null 2>&1 || true
fi
for model in "$main_model" "$pii_model" ai-curator-qwen3-4b ai-curator-qwen3-8b ai-curator-gemma4-12b ai-curator-pii-gemma3-4b; do
  ollama stop "$model" >/dev/null 2>&1 || true
done

if [ "$runtime" = llama_cpp ]; then
  docker compose --profile llama --profile demo up --build -d app model mock-jira
else
  docker compose --profile demo up --build -d --no-deps app mock-jira
fi
printf 'ИИ-Куратор запускается (%s). Откройте http://127.0.0.1:%s\n' "$runtime" "$port"
