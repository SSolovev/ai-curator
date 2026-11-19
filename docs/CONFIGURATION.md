# Конфигурация

Все обычные параметры имеют prefix `CURATOR_`. Секреты не задаются env-переменными.

| Параметр | Default | Назначение |
|---|---:|---|
| `CURATOR_PORT` | `8080` | loopback host port Compose |
| `CURATOR_HOST` | `127.0.0.1` (`0.0.0.0` внутри container) | адрес UI |
| `CURATOR_ALLOWED_HOSTS` | localhost/loopback | Trusted Host allowlist |
| `CURATOR_LLM_RUNTIME` | `llama_cpp` | единственный runtime процесса: `llama_cpp`, `ollama` или `external` |
| `CURATOR_LLM_BASE_URL` | `http://model:8080` | адрес llama.cpp; внешние host отклоняются |
| `CURATOR_LLM_MODEL` | `local-model` | model name llama.cpp для API |
| `CURATOR_OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | адрес native Ollama с точки зрения app container |
| `CURATOR_OLLAMA_MODEL` | `ai-curator-gemma4-12b` | основной локальный тег Ollama; `ai-curator-qwen3-8b` остаётся более лёгкой альтернативой |
| `CURATOR_EXTERNAL_LLM_ENABLED` | `false` | обязательное явное разрешение runtime `external` |
| `CURATOR_EXTERNAL_LLM_BASE_URL` | `https://api.openai.com/v1` | HTTPS base URL OpenAI-compatible API без credentials/query |
| `CURATOR_EXTERNAL_LLM_MODEL` | `gpt-4.1-mini` | модель внешнего API |
| `CURATOR_EXTERNAL_LLM_API_KEY_FILE` | `/secrets/external_llm_api_key` | путь к secret-файлу API-ключа |
| `CURATOR_PII_FILTER_BASE_URL` | `http://host.docker.internal:11434` | локальный Ollama endpoint обязательного PII-фильтра всех runtime |
| `CURATOR_PII_FILTER_MODEL` | `ai-curator-pii-gemma3-4b` | отдельная локальная модель обнаружения персональных данных перед любой основной моделью |
| `CURATOR_PII_FILTER_DICTIONARY_FILE` | `/secrets/pii_dictionary.txt` | обязательный для всех runtime непустой словарь локальных замен |
| `CURATOR_PII_FILTER_TIMEOUT_SECONDS` | `300` | timeout одного локального PII-вызова |
| `CURATOR_PII_FILTER_BATCH_ITEMS` | `20` | максимум текстовых фрагментов в партии PII-проверки |
| `CURATOR_PII_FILTER_BATCH_CHARS` | `12000` | text budget партии PII-проверки |
| `CURATOR_LLM_TIMEOUT_SECONDS` | `240` | timeout generation |
| `CURATOR_LLM_TEMPERATURE` | `0.2` | детерминированность и фактологическая устойчивость генерации |
| `CURATOR_LLM_MAX_TOKENS` | `6000` | общий предел thinking и финального ответа |
| `CURATOR_LLM_REASONING_EFFORT` | `none` | thinking-профиль Ollama: `none`, `low`, `medium`, `high`; для Gemma 4 по умолчанию отключён, чтобы reasoning не исчерпывал лимит ответа |
| `CURATOR_LLM_DIRECT_PROMPT_CHARS` | `18000` | порог прямой генерации по суммарной длине сообщений и JSON Schema в символах; `0` включает предварительное сжатие всегда, когда есть комментарии |
| `CURATOR_LLM_COMMENT_BATCH_CHARS` | `20000` | text budget партии извлечения; лимита количества комментариев нет |
| `CURATOR_LLM_REPAIR_ENABLED` | `false` | повторная полная генерация после ошибки валидации |
| `CURATOR_LLM_LOG_REQUEST_RESPONSE` | `false` | логировать точный уже отфильтрованный запрос и ответ основной модели для проверки обезличивания |
| `CURATOR_JIRA_LOG_REQUEST_RESPONSE` | `false` | временно логировать полный запрос и ответ Jira без маскирования значений |
| `CURATOR_JIRA_LOG_INVALID_RESPONSE_BODY` | `false` | временно логировать тело не-JSON ответа Jira |
| `CURATOR_JIRA_LOG_FIELD_CATALOG` | `false` | временно логировать полный ответ `/rest/api/2/field` |
| `CURATOR_JIRA_LOG_EMPLOYEE_ISSUE` | `false` | временно логировать ограниченный ответ выбранной Employee-задачи |
| `CURATOR_JIRA_LOG_RESPONSE_MAX_CHARS` | `4000` | максимальная длина диагностического тела ответа |
| `CURATOR_JIRA_PAGE_SIZE` | `50` | Jira page size, максимум 100 |
| `CURATOR_JIRA_MAX_PAGES` | `100` | защита от бесконечной pagination |
| `CURATOR_JIRA_MAX_COMMENTS` | `50` | максимум 50 последних комментариев Employee-задачи для итогового синтеза, напрямую или после предварительного сжатия |
| `CURATOR_MAX_COMMENT_CHARS` | `12000` | лимит одного комментария |
| `CURATOR_MAX_CONTEXT_CHARS` | `80000` | лимит дополнительной feedback-истории; полученные комментарии не удаляются и сжимаются отдельным этапом |
| `CURATOR_MAX_JIRA_RESPONSE_BYTES` | `10000000` | лимит одного response |
| `CURATOR_MOOD_ISSUE_TYPE_NAMES` | `["Mood Monitoring"]` | точные names MM issue types |
| `CURATOR_FEEDBACK_ISSUE_TYPE_NAMES` | `["Feedback","KPI"]` | точные names feedback issue types |
| `LLAMA_THREADS` | `8` | CPU threads в Compose |
| `LLAMA_GPU_LAYERS` | `0` | CPU fallback; менять только после проверки backend |

### Переключение runtime при запуске

Задайте в `.env` ровно один режим и повторите `./scripts/start.sh` (`scripts/start.ps1` в PowerShell):

```dotenv
CURATOR_LLM_RUNTIME=ollama
```

- `llama_cpp` — Qwen3 4B в Docker, CPU fallback;
- `ollama` — выбранная через `CURATOR_OLLAMA_MODEL` модель в native Ollama с Metal на Apple Silicon;
- `external` — внешний OpenAI-compatible HTTPS API.

Перед каждым runtime работает один и тот же обязательный локальный PII- и словарный фильтр. Поэтому native Ollama, тег `ai-curator-pii-gemma3-4b` и непустой `secrets/pii_dictionary.txt` требуются даже для `llama_cpp`.

Runtime нельзя менять из браузера и нельзя менять без перезапуска. Backend создаёт только один выбранный адаптер. `scripts/start.*` перед запуском останавливает контейнер `llama.cpp` для Ollama, а для `llama_cpp` выгружает AI Curator-модели из памяти Ollama. Фоновый процесс Ollama не завершается целиком, потому что им могут пользоваться другие приложения.

Сначала всегда подготовьте PII-фильтр, затем нужную основную модель Ollama, если выбран runtime `ollama`:

```bash
./scripts/prepare-ollama.sh pii-filter
./scripts/prepare-ollama.sh gemma4-12b
./scripts/prepare-ollama.sh qwen3-8b
# либо обе последовательно
./scripts/prepare-ollama.sh all
curl -fsS http://127.0.0.1:11434/api/tags
```

Для Gemma скрипт выполняет `ollama pull gemma4:12b`, скачивает официальный Q4_K_M-пакет Ollama размером около 7,6 ГБ и создаёт тег `ai-curator-gemma4-12b` с контекстом 16K. Для Qwen скрипт импортирует уже находящийся в `models/` GGUF и создаёт тег `ai-curator-qwen3-8b`; сеть не используется. Модель не скачивается при обычном запуске приложения. Ollama должна быть запущена на хосте. На Docker Desktop приложение обращается к ней через `host.docker.internal`.

Для переключения измените только две строки `.env`:

```dotenv
CURATOR_LLM_RUNTIME=ollama
CURATOR_OLLAMA_MODEL=ai-curator-gemma4-12b
```

Для более лёгкой Qwen укажите `CURATOR_OLLAMA_MODEL=ai-curator-qwen3-8b`. После каждого переключения выполните `./scripts/start.sh` или `scripts/start.ps1`. Пересоздавать тег модели при каждом переключении не нужно; `prepare-ollama.*` повторяется только после изменения Modelfile или если тег ещё не создан.

Внешний режим и API-key secret описаны отдельно в [EXTERNAL_LLM.md](EXTERNAL_LLM.md). Общий privacy-контур одинаково обязателен для локальных и внешнего runtime.

На MacBook Air M3 тестовый полный сценарий Qwen3 8B занял около 5 минут 39 секунд при 2155 выходных токенах. Генерация выполняется фоновой задачей: start endpoint сразу возвращает `generation_id`, а UI получает состояние коротким polling. Если модель уже занята, статус новой задачи завершится ошибкой `LLM_BUSY`.

Для штатного переключения не запускайте model container вручную: измените ключ и повторите `scripts/start.*`. `scripts/stop.*` останавливает Compose и выгружает все известные AI Curator-модели из Ollama.

### Jira TLS и proxy

`jira_base_url` вводится без `/rest/api/...`, query, username/password. Для реальной Jira требуется HTTPS. Поместите публичный CA bundle в `certs/` и укажите внутри UI путь `/certs/company-ca.pem`. TLS verification не отключается. Proxy URL вводится отдельно и передаётся httpx.

`CURATOR_JIRA_LOG_INVALID_RESPONSE_BODY=true` следует включать только на время диагностики. В лог попадёт до указанного лимита символов реального не-JSON ответа Jira; PAT и распространённые token/password-поля маскируются, но HTML может содержать персональные данные. После диагностики верните значение `false` и пересоздайте `app`.

`CURATOR_JIRA_LOG_FIELD_CATALOG=true` записывает полный успешный ответ Jira `/rest/api/2/field` событием `jira_field_catalog_response`. Каталог может содержать внутренние названия и описания пользовательских полей, поэтому после сопоставления верните значение `false`.

`CURATOR_JIRA_LOG_EMPLOYEE_ISSUE=true` записывает ограниченный mapped-полями ответ выбранной Employee-задачи событием `jira_employee_issue_response`. Этот ответ содержит фактические значения полей и может включать персональные данные. Флаг не расширяет запрос до `fields=*all`; после диагностики обязательно верните значение `false`.

`CURATOR_JIRA_LOG_REQUEST_RESPONSE=true` записывает каждый Jira GET-запрос событием `jira_request_payload` и полный текст каждого полученного ответа событием `jira_response_payload`. Значения URL, query-параметров и ответа не маскируются. Заголовки запроса, `Authorization` и PAT намеренно не записываются. Режим действует также для повторных попыток и может создать очень большие логи с персональными данными.

`CURATOR_LLM_LOG_REQUEST_RESPONSE=true` для локальных runtime записывает точный прошедший privacy-фильтр OpenAI-compatible payload событиями `llm_request_payload` и `llm_response_payload`. Для `external` события называются `external_llm_request_payload` и `external_llm_response_payload`. Сырой запрос локальной PII-модели никогда не логируется. Даже отфильтрованные payload могут содержать внутреннюю рабочую информацию и косвенные идентификаторы, поэтому доступ к логам нужно ограничить. Событие `pii_filter_completed` с числом замен должно предшествовать каждому запросу основной модели.

### Секрет-file

Опционально создайте `secrets/jira_token`, одна строка без кавычек, и ограничьте права (`chmod 600`). Если поле PAT в UI пустое, backend прочитает этот файл. Не включайте его в offline bundle.

### GPU

Default `LLAMA_GPU_LAYERS=0` работает на CPU. NVIDIA CUDA требует отдельного официального CUDA image digest, NVIDIA Container Toolkit и Compose device reservation; это не включено в default, потому что passthrough зависит от ОС. На Apple Silicon Docker container не получает Metal; native Ollama использует доступное ей ускорение Metal. Адреса основных локальных моделей и PII-фильтра принимают только `model`, loopback и `host.docker.internal`. Внешний host разрешается исключительно отдельным `CURATOR_EXTERNAL_LLM_BASE_URL`, требует HTTPS и активируется двойной настройкой `CURATOR_LLM_RUNTIME=external` плюс `CURATOR_EXTERNAL_LLM_ENABLED=true`.
