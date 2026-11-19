#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

command -v ollama >/dev/null 2>&1 || {
  echo "Ollama не найдена. Установите и запустите native Ollama."
  exit 1
}

prepare_local_gguf_model() {
  model_name=$1
  gguf_name=$2
  modelfile_name=$3
  test -f "$project_dir/models/$gguf_name" || {
    echo "Не найден models/$gguf_name"
    exit 1
  }
  test -f "$project_dir/models/$modelfile_name" || {
    echo "Не найден models/$modelfile_name"
    exit 1
  }
  ollama create "$model_name" -f "$project_dir/models/$modelfile_name"
  ollama show "$model_name" >/dev/null
}

prepare_registry_model() {
  model_name=$1
  source_name=$2
  modelfile_name=$3
  test -f "$project_dir/models/$modelfile_name" || {
    echo "Не найден models/$modelfile_name"
    exit 1
  }
  ollama pull "$source_name"
  ollama create "$model_name" -f "$project_dir/models/$modelfile_name"
  ollama show "$model_name" >/dev/null
}

test "$#" -le 1 || {
  echo "Использование: $0 [gemma4-12b|qwen3-8b|pii-filter|all]"
  exit 1
}

target=${1:-gemma4-12b}

case "$target" in
  qwen3-8b)
    prepare_local_gguf_model ai-curator-qwen3-8b Qwen3-8B-Q4_K_M.gguf Modelfile.qwen3-8b
    ;;
  gemma4-12b)
    prepare_registry_model ai-curator-gemma4-12b gemma4:12b Modelfile.gemma4-12b
    ;;
  pii-filter)
    prepare_registry_model ai-curator-pii-gemma3-4b gemma3:4b Modelfile.pii-gemma3-4b
    ;;
  all)
    prepare_local_gguf_model ai-curator-qwen3-8b Qwen3-8B-Q4_K_M.gguf Modelfile.qwen3-8b
    prepare_registry_model ai-curator-gemma4-12b gemma4:12b Modelfile.gemma4-12b
    prepare_registry_model ai-curator-pii-gemma3-4b gemma3:4b Modelfile.pii-gemma3-4b
    ;;
  *)
    echo "Неизвестная модель: $target"
    echo "Использование: $0 [gemma4-12b|qwen3-8b|pii-filter|all]"
    exit 1
    ;;
esac

echo "Подготовка Ollama завершена. Выберите CURATOR_OLLAMA_MODEL в .env и выполните ./scripts/start.sh."
