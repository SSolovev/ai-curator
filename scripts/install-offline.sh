#!/usr/bin/env sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$bundle_dir"
shasum -a 256 -c BUNDLE_SHA256SUMS
docker load -i images.tar
mkdir -p secrets certs
umask 077
test -s secrets/hmac_key || { openssl rand -hex 32 > secrets/hmac_key; }
cp .env.example .env 2>/dev/null || true
docker compose up -d
echo "Откройте http://127.0.0.1:8080"

