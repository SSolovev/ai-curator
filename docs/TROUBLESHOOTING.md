# Диагностика

## Docker Desktop не может подключиться к Docker Hub

Ошибка `no route to host` при обращении к `registry-1.docker.io:443` означает, что сборщик не смог подключиться к реестру. Если в ошибке указан `docker/dockerfile:1.7`, проверьте, что используете актуальный Dockerfile. Он использует встроенный обработчик Dockerfile в BuildKit. Чистая сборка по-прежнему требует доступа к базовому образу Python и источникам Python-зависимостей. [Обработчик Dockerfile](https://docs.docker.com/reference/dockerfile/#syntax)

Если сеть требует прокси, настройте его в **Docker Desktop → Settings → Resources → Proxies**. Повторите `docker compose build app` для проверки. [Настройки прокси Docker Desktop](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies)

Если образ приложения уже собран, в режимах `external` и `ollama` можно запустить его вместе с демо Jira без пересборки.

```bash
docker image inspect ai-curator-app:0.1.0 --format '{{.Created}}'
docker compose --profile demo up -d --no-build --pull never app mock-jira
```

Используются код из имеющегося образа и текущие настройки `.env`. Для `llama_cpp` добавьте `--profile llama` перед `up` и сервис `model` в конец команды. В этом случае образ `model` и файл весов тоже должны быть на компьютере.

## Где смотреть логи

Из корня проекта сначала проверьте состояние контейнеров. Затем откройте логи приложения, воспроизведите проблему на демо Jira и сопоставьте события по `correlation_id`.

```bash
docker compose --profile llama --profile demo ps -a
docker compose logs --tail 100 app
docker compose logs -f --since 10m app
```

`Ctrl+C` завершает просмотр. Используйте логи нужного контейнера, если проблема относится к Docker-модели или демо Jira.

```bash
docker compose --profile llama logs --tail 100 model
docker compose --profile demo logs --tail 100 mock-jira
```

Для диагностики генерации без тел запросов оставьте в выводе события с техническими метаданными.

```bash
docker compose logs --no-log-prefix --since 10m app | rg '"event": "(pii_filter_completed|pii_filter_failed|llm_response_metadata|external_llm_response_metadata|comment_extraction_failed|background_generation_completed)"'
```

- `background_generation_completed` содержит итоговый `code` и время задачи.
- `pii_filter_completed` показывает, что локальный фильтр обработал контекст перед вызовом основной модели.
- `pii_filter_failed` содержит причину сбоя обращения к модели обезличивания или проверки её ответа. Код `PII_FILTER_SPAN_MISMATCH` означает, что возвращённого фрагмента нет в указанной входной записи.
- `llm_response_metadata` и `external_llm_response_metadata` содержат число токенов и `finish_reason`. Для итогового сценария смотрите `operation=generate`.

### Логи Ollama на компьютере

```bash
# macOS
tail -n 100 -f ~/.ollama/logs/server.log

# Linux с systemd
journalctl -u ollama -n 100 -f
```

```powershell
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 100 -Wait
```

Если Ollama запущена командой `ollama serve`, смотрите вывод в её терминале. Команда `ollama list` показывает установленные теги, `ollama ps` — загруженные модели. [Руководство Ollama по логам](https://docs.ollama.com/troubleshooting)

## Jira отклонила token (401)

Создайте новый PAT по правилам вашей Jira, проверьте отсутствие пробелов и VPN. Не отправляйте token в чат/скриншот/лог. 403 означает недостаток прав; 404 может означать отсутствие записи или прав видеть её.

## TLS certificate

Не отключайте verification. Получите PEM CA bundle у IT, положите в `certs/` и в UI укажите `/certs/<имя>.pem`. Ошибка `JIRA_TLS` обычно означает неправильный bundle/цепочку или TLS interception proxy.

## Proxy/VPN

Proxy вводится в расширенных настройках подключения. Проверьте, что proxy разрешает Jira и не заменяет сертификат без доверенного CA. Model container в интернет ходить не должен.

## Модель долго запускается / мало RAM

Первый запуск может занять несколько минут. Для Docker-модели проверьте `docker compose --profile llama ps` и логи `model`. Если основной модели Ollama не хватает памяти, попробуйте профиль Qwen3 8B или Docker-профиль Qwen3 4B. Обязательная PII-модель также использует локальную память.

## Model runtime unhealthy

Сначала проверьте активное значение `CURATOR_LLM_RUNTIME` в `.env`. После любого изменения повторно выполните `scripts/start.*`; переключения из UI нет.

Для `llama.cpp (Docker)` проверьте `docker compose --profile llama ps` и `docker compose logs model --tail 100`, наличие `models/Qwen3-4B-Q4_K_M.gguf`, checksum `cd models && shasum -a 256 -c SHA256SUMS`. Runtime port намеренно не доступен с host.

Для `Ollama (Native)` проверьте, что приложение Ollama запущено, а `ollama list` содержит выбранный в `CURATOR_OLLAMA_MODEL` тег: `ai-curator-gemma4-12b:latest` или `ai-curator-qwen3-8b:latest`. Если тега нет, выполните `./scripts/prepare-ollama.sh gemma4-12b` либо `./scripts/prepare-ollama.sh qwen3-8b`. Проверить доступ из app container можно так:

```bash
docker compose exec app python -c 'import httpx; print(httpx.get("http://host.docker.internal:11434/api/tags", timeout=10).status_code)'
```

Ожидаемый статус — `200`. Не заменяйте адрес внешним host и не публикуйте Ollama на сетевом интерфейсе без firewall и согласования с IT.

В блоке «Модель и защита данных» проверьте основную модель и фильтр по отдельности. `/healthz` подтверждает запуск приложения. Для проверки фильтра найдите тег из `CURATOR_PII_FILTER_MODEL` через `ollama show` и убедитесь, что словарь непустой. Для `external` проверьте файл API-ключа и права доступа к API. Ошибка `PII_FILTER_FAILED` останавливает запрос до вызова основной модели.

## Windows/WSL2

Убедитесь, что проект находится в доступном Docker Desktop каталоге и WSL integration включена. CPU fallback работает; CUDA требует совместимые NVIDIA driver, WSL2 и Container Toolkit — default Compose этого не обещает.

## Сценарий отклонён валидатором

Если в контексте есть источники, схема ответа перечисляет допустимые обезличенные `source_ref`. Сервер восстанавливает исходные ссылки и проверяет, что каждая из них есть в выбранном контексте и что у фактов есть `evidence`. Если модель вернёт ссылку вне списка, сервер отклонит сценарий с кодом `LLM_SOURCE_UNKNOWN`. Значение `CURATOR_LLM_REPAIR_ENABLED=true` разрешает одну попытку исправления.

## Внешняя модель исчерпала лимит ответа

`EXTERNAL_LLM_OUTPUT_TRUNCATED` соответствует `finish_reason=length`. Проверьте `completion_tokens` в событии `external_llm_response_metadata`. Для Qwen в Yandex AI Studio начните с `CURATOR_LLM_REASONING_EFFORT=none`, затем при повторном обрыве скорректируйте `CURATOR_LLM_MAX_TOKENS`.

## Комментарий не появился в Jira

`JIRA_COMMENT_FORBIDDEN` означает, что у учётной записи нет права добавлять комментарии в задачу. При `JIRA_CONNECTION_CHANGED` сформируйте сценарий заново с текущим подключением. При `JIRA_COMMENT_UNCONFIRMED` откройте исходную задачу Jira и проверьте, появился ли комментарий, прежде чем повторять отправку.

## Failed to fetch во время генерации

Генерация работает как фоновая задача, а браузер выполняет короткий polling раз в три секунды. Единичный сетевой сбой повторяется автоматически. Если сообщение остаётся после нескольких попыток, проверьте `docker compose ps`, обновите страницу: активный `generation_id` хранится в `sessionStorage`, и ожидание продолжится в той же сессии. После перезапуска backend незавершённые задачи восстановить нельзя.

## Полный запрос, ответ и время этапов LLM

Установите `CURATOR_LLM_LOG_REQUEST_RESPONSE=true` в `.env` и пересоздайте `app`. Payload содержит точные данные, переданные основной модели уже после обязательной фильтрации. Он нужен для оценки деперсонализации, но всё ещё может содержать косвенные идентификаторы и внутреннюю информацию.

```bash
docker compose build app
docker compose up -d --no-deps --force-recreate app
docker compose logs --no-log-prefix -f app | rg 'llm_'
```

События `llm_request_payload` и `llm_response_payload` содержат точный отфильтрованный запрос и ответ локальной основной модели. Для внешнего runtime используются `external_llm_request_payload` и `external_llm_response_payload`; сырой ввод PII-модели не логируется. В одной `correlation_id` событие `pii_filter_completed` должно появиться раньше request payload. Длительности в миллисекундах находятся в `pii_filter_completed`, `llm_data_preparation_completed`, `llm_model_response_completed`, `llm_postprocessing_completed` и `llm_validation_completed`. `operation` различает извлечение (`comment_extraction`), генерацию (`generate`) и исправление (`repair`).

После проверки верните `CURATOR_LLM_LOG_REQUEST_RESPONSE=false` и снова пересоздайте `app`.

## Полный запрос и ответ Jira

Временно установите `CURATOR_JIRA_LOG_REQUEST_RESPONSE=true` в `.env`, пересоздайте `app` и смотрите события:

```bash
docker compose up -d --no-deps --force-recreate app
docker compose logs --no-log-prefix -f app | rg 'jira_(request|response)_payload'
```

`jira_request_payload` содержит полный URL и query-параметры, а `jira_response_payload` — статус и полный текст ответа без маскирования. Заголовок `Authorization` и PAT не логируются. После диагностики верните флаг в `false` и снова пересоздайте `app`.
