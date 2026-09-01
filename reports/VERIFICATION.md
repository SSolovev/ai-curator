# Выполненные проверки — 2026-08-12

- `pytest + coverage`: 47 passed, coverage 82%.
- `ruff check`: passed.
- `pip check`: no broken requirements.
- `pip-audit` production lock: 0 known vulnerabilities after upgrading FastAPI/Starlette/python-multipart. Report: `pip-audit.json`.
- CycloneDX JSON SBOM: 29 production components. Report: `sbom.cdx.json`.
- Python compile/import checks: passed through tests and `compileall`.
- Python package build: `ai_curator-0.1.0-py3-none-any.whl` built successfully with the pinned local build backend.
- Browser smoke-test: локальный UI открылся, mock Jira подключилась через `/myself`, список сотрудников, mapping и preview отработали; в console не было warning/error. Проверка выполнялась без реальной Jira и без передачи данных наружу.

Не выполнены в этой среде:

- Docker/Compose build и container smoke-test: Docker/Podman не установлены.
- Реальный Qwen3-8B benchmark: model weights отсутствуют, Docker/runtime отсутствуют.
- OS/base-image/llama.cpp scan через Grype/Trivy: scanners не установлены.
- Реальная корпоративная Jira: автоматические тесты намеренно используют только mock.

Для этих проверок подготовлены `scripts/smoke-container.sh`, `scripts/benchmark-model.sh` и `scripts/security-report.sh`.
