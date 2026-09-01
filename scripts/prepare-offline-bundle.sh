#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bundle_dir="$project_dir/offline-bundle"
model_file="$project_dir/models/Qwen3-4B-Q4_K_M.gguf"
test -f "$model_file" || { echo "Сначала подготовьте модель."; exit 1; }
mkdir -p "$bundle_dir/models"
docker build -t ai-curator-app:0.1.0 "$project_dir"
docker pull ghcr.io/ggml-org/llama.cpp:server-b9445@sha256:8dd148c53936b6e8b0e75309841e66eab13adc50b004a5e86ab1fec477c17d8e
docker save -o "$bundle_dir/images.tar" ai-curator-app:0.1.0 ghcr.io/ggml-org/llama.cpp:server-b9445@sha256:8dd148c53936b6e8b0e75309841e66eab13adc50b004a5e86ab1fec477c17d8e
cp "$model_file" "$bundle_dir/models/"
cp "$project_dir/models/SHA256SUMS" "$project_dir/compose.yaml" "$project_dir/.env.example" "$bundle_dir/"
cp "$project_dir/scripts/install-offline.sh" "$bundle_dir/"
(cd "$bundle_dir" && find . -type f ! -name BUNDLE_SHA256SUMS -exec shasum -a 256 {} \; > BUNDLE_SHA256SUMS)
echo "Offline bundle создан: $bundle_dir. Проверьте разрешение на распространение весов Apache-2.0 и внутреннюю политику."

