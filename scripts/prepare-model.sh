#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
model_file="$project_dir/models/Qwen3-4B-Q4_K_M.gguf"
model_url="https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf"
mkdir -p "$project_dir/models" "$project_dir/secrets" "$project_dir/certs"
if [ ! -s "$project_dir/secrets/hmac_key" ]; then
  umask 077
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32 > "$project_dir/secrets/hmac_key"; else python3 -c 'import secrets; print(secrets.token_hex(32))' > "$project_dir/secrets/hmac_key"; fi
fi
if [ ! -f "$model_file" ]; then
  echo "Скачивание модели один раз (5.03 GB)…"
  curl --fail --location --continue-at - --output "$model_file.part" "$model_url"
  mv "$model_file.part" "$model_file"
fi
cd "$project_dir/models"
if command -v sha256sum >/dev/null 2>&1; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi
echo "Модель подготовлена и проверена. При следующих запусках сеть ей не нужна."

