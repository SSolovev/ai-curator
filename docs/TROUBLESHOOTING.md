# Диагностика

## Jira отклонила token (401)

Создайте новый PAT по правилам вашей Jira, проверьте отсутствие пробелов и VPN. Не отправляйте token в чат/скриншот/лог. 403 означает недостаток прав; 404 может означать отсутствие записи или прав видеть её.

## TLS certificate

Не отключайте verification. Получите PEM CA bundle у IT, положите в `certs/` и в UI укажите `/certs/<имя>.pem`. Ошибка `JIRA_TLS` обычно означает неправильный bundle/цепочку или TLS interception proxy.

## Proxy/VPN

Proxy вводится в расширенных настройках подключения. Проверьте, что proxy разрешает Jira и не заменяет сертификат без доверенного CA. Model container в интернет ходить не должен.

## Модель долго запускается / мало RAM

Первый health может ждать до трёх минут. Закройте тяжёлые приложения, оставьте `LLAMA_GPU_LAYERS=0`, проверьте 32 ГБ RAM. Если memory pressure остаётся, используйте Qwen3-4B Q4_K_M после обновления model/checksum/config и повторного quality benchmark.

## Model runtime unhealthy

Сначала проверьте активное значение `CURATOR_LLM_RUNTIME` в `.env`. После любого изменения повторно выполните `scripts/start.*`; переключения из UI нет.

Для `llama.cpp (Docker)` проверьте `docker compose --profile llama ps` и `docker compose logs model --tail 100`, наличие `models/Qwen3-4B-Q4_K_M.gguf`, checksum `cd models && shasum -a 256 -c SHA256SUMS`. Runtime port намеренно не доступен с host.

Для `Ollama (Native)` проверьте, что приложение Ollama запущено, а `ollama list` содержит выбранный в `CURATOR_OLLAMA_MODEL` тег: `ai-curator-gemma4-12b:latest` или `ai-curator-qwen3-8b:latest`. Если тега нет, выполните `./scripts/prepare-ollama.sh gemma4-12b` либо `./scripts/prepare-ollama.sh qwen3-8b`. Проверить доступ из app container можно так:

```bash
docker compose exec app python -c 'import httpx; print(httpx.get("http://host.docker.internal:11434/api/tags", timeout=10).status_code)'
```

Ожидаемый статус — `200`. Не заменяйте адрес внешним host и не публикуйте Ollama на сетевом интерфейсе без firewall и согласования с IT.

Для любого runtime диагностика должна показать `health.ok=true` одновременно у основной модели и у `privacy_filter`. Проверьте непустой `secrets/pii_dictionary.txt` и наличие `ai-curator-pii-gemma3-4b` через `ollama show`. Для `external` дополнительно проверьте `secrets/external_llm_api_key`. Ошибка `PII_FILTER_FAILED` означает, что основная модель намеренно не была вызвана.

## Windows/WSL2

Убедитесь, что проект находится в доступном Docker Desktop каталоге и WSL integration включена. CPU fallback работает; CUDA требует совместимые NVIDIA driver, WSL2 и Container Toolkit — default Compose этого не обещает.

## Сценарий отклонён валидатором

Если в контексте есть источники, схема ответа перечисляет допустимые обезличенные `source_ref`. Сервер восстанавливает исходные ссылки и проверяет, что каждая из них есть в выбранном контексте и что у фактов есть `evidence`. Если модель вернёт ссылку вне списка, сервер отклонит сценарий с кодом `LLM_SOURCE_UNKNOWN`. Значение `CURATOR_LLM_REPAIR_ENABLED=true` разрешает одну попытку исправления.

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
