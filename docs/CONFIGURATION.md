# Конфигурация

Все обычные параметры имеют prefix `CURATOR_`. Секреты не задаются env-переменными.

| Параметр | Default | Назначение |
|---|---:|---|
| `CURATOR_PORT` | `8080` | loopback host port Compose |
| `CURATOR_HOST` | `127.0.0.1` (`0.0.0.0` внутри container) | адрес UI |
| `CURATOR_ALLOWED_HOSTS` | localhost/loopback | Trusted Host allowlist |
| `CURATOR_LLM_BASE_URL` | `http://model:8080` | заменяемый локальный runtime; внешние host отклоняются |
| `CURATOR_LLM_MODEL` | `local-model` | model name для API |
| `CURATOR_LLM_TIMEOUT_SECONDS` | `240` | timeout generation |
| `CURATOR_LLM_REPAIR_ENABLED` | `false` | повторная полная генерация после ошибки валидации |
| `CURATOR_JIRA_LOG_INVALID_RESPONSE_BODY` | `false` | временно логировать тело не-JSON ответа Jira |
| `CURATOR_JIRA_LOG_FIELD_CATALOG` | `false` | временно логировать полный ответ `/rest/api/2/field` |
| `CURATOR_JIRA_LOG_EMPLOYEE_ISSUE` | `false` | временно запрашивать `*all` и логировать выбранную Employee-задачу |
| `CURATOR_JIRA_LOG_RESPONSE_MAX_CHARS` | `4000` | максимальная длина диагностического тела ответа |
| `CURATOR_JIRA_PAGE_SIZE` | `50` | Jira page size, максимум 100 |
| `CURATOR_JIRA_MAX_PAGES` | `100` | защита от бесконечной pagination |
| `CURATOR_MAX_COMMENT_CHARS` | `12000` | лимит одного комментария |
| `CURATOR_MAX_CONTEXT_CHARS` | `80000` | общий text budget до LLM |
| `CURATOR_MAX_JIRA_RESPONSE_BYTES` | `10000000` | лимит одного response |
| `CURATOR_MOOD_ISSUE_TYPE_NAMES` | `["Mood Monitoring"]` | точные names MM issue types |
| `CURATOR_FEEDBACK_ISSUE_TYPE_NAMES` | `["Feedback","KPI"]` | точные names feedback issue types |
| `LLAMA_THREADS` | `8` | CPU threads в Compose |
| `LLAMA_GPU_LAYERS` | `0` | CPU fallback; менять только после проверки backend |

### Jira TLS и proxy

`jira_base_url` вводится без `/rest/api/...`, query, username/password. Для реальной Jira требуется HTTPS. Поместите публичный CA bundle в `certs/` и укажите внутри UI путь `/certs/company-ca.pem`. TLS verification не отключается. Proxy URL вводится отдельно и передаётся httpx.

`CURATOR_JIRA_LOG_INVALID_RESPONSE_BODY=true` следует включать только на время диагностики. В лог попадёт до указанного лимита символов реального не-JSON ответа Jira; PAT и распространённые token/password-поля маскируются, но HTML может содержать персональные данные. После диагностики верните значение `false` и пересоздайте `app`.

`CURATOR_JIRA_LOG_FIELD_CATALOG=true` записывает полный успешный ответ Jira `/rest/api/2/field` событием `jira_field_catalog_response`. Каталог может содержать внутренние названия и описания пользовательских полей, поэтому после сопоставления верните значение `false`.

`CURATOR_JIRA_LOG_EMPLOYEE_ISSUE=true` временно запрашивает `fields=*all` и записывает полный ответ выбранной Employee-задачи событием `jira_employee_issue_response`. Этот ответ содержит фактические значения полей и может включать персональные данные. После сопоставления обязательно верните значение `false`.

### Секрет-file

Опционально создайте `secrets/jira_token`, одна строка без кавычек, и ограничьте права (`chmod 600`). Если поле PAT в UI пустое, backend прочитает этот файл. Не включайте его в offline bundle.

### GPU

Default `LLAMA_GPU_LAYERS=0` работает на CPU. NVIDIA CUDA требует отдельного официального CUDA image digest, NVIDIA Container Toolkit и Compose device reservation; это не включено в default, потому что passthrough зависит от ОС. На Apple Silicon Docker container не получает Metal; для ускорения запускайте native `llama-server` и используйте разрешённый `host.docker.internal` endpoint. Приложение принимает для модели только `model`, loopback и `host.docker.internal`; внешний AI host отклоняется при старте.
