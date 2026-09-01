#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$project_dir/reports"
cd "$project_dir"
auditor=""
if [ -x "$project_dir/.venv/bin/pip-audit" ]; then
  auditor="$project_dir/.venv/bin/pip-audit"
elif command -v pip-audit >/dev/null 2>&1; then
  auditor="pip-audit"
fi
if [ -n "$auditor" ]; then
  "$auditor" -r requirements.prod.lock --no-deps --disable-pip --format json --output reports/pip-audit.json || true
  "$auditor" -r requirements.prod.lock --no-deps --disable-pip --format cyclonedx-json --output reports/sbom.cdx.json || true
else
  echo "pip-audit не установлен; Python CVE report и CycloneDX SBOM не сформированы." > reports/pip-audit.NOT_RUN.txt
fi
if command -v syft >/dev/null 2>&1; then syft dir:. -o cyclonedx-json=reports/filesystem-sbom.cdx.json; fi
if command -v grype >/dev/null 2>&1; then grype dir:. -o json > reports/grype.json || true; else echo "grype не установлен; filesystem CVE scan не выполнялся." > reports/grype.NOT_RUN.txt; fi
echo "Отчёты сохранены в reports/. Не публикуйте их без проверки."
