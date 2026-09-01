# ИИ-Куратор

Локальное privacy-first приложение для подготовки бережной 30-минутной встречи 1:1. Оно читает доступные куратору данные Jira, даёт проверить контекст, запускает локальный `llama.cpp` и показывает только сценарий, прошедший JSON Schema и дополнительные безопасностные инварианты.

Данные Jira и inference не уходят во внешние AI API. Jira используется только на чтение. Приложение не предназначено для диагностики или автоматических кадровых решений.

## Быстрый старт

Нужны Docker Desktop (Windows 11/WSL2 — основной сценарий; также Linux/macOS), 32 ГБ RAM и около 12 ГБ свободного диска.

```bash
cp .env.example .env
./scripts/prepare-model.sh
./scripts/start.sh
```

Откройте <http://127.0.0.1:8080>. Jira PAT введите в форме: по умолчанию он останется только в памяти сессии. Для Docker secret создайте `secrets/jira_token` с правами только владельца; этот путь исключён из Git.

Полная простая инструкция: [docs/INSTALL_MANAGER.md](docs/INSTALL_MANAGER.md). Архитектура и границы: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Проверка без реальной Jira

После запуска добавьте mock Jira:

```bash
docker compose --profile demo up -d mock-jira
```

В UI укажите `http://mock-jira:9000` и токен `demo-token`. Mock доступен только локально и содержит исключительно вымышленные данные.

## Разработка и тесты

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
./scripts/test.sh
```

Container smoke-test: `./scripts/smoke-container.sh`. Model benchmark после подготовки весов: `./scripts/benchmark-model.sh`.

## Важные каталоги

- `src/ai_curator` — приложение и адаптеры;
- `prompts` и `schemas` — версионируемые LLM-контракты;
- `tests/fixtures` — обезличенный golden-кейс;
- `models` — локальные веса (не входят в репозиторий/образ);
- `data`/Docker volume — только mapping полей; исходные персональные данные автоматически не сохраняются.
