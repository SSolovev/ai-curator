# Внешняя LLM и локальное обезличивание

Режим `external` предназначен для OpenAI Chat Completions API и совместимых с ним HTTPS-сервисов. Он выключен по умолчанию и требует отдельного разрешения вашей организации на передачу обезличенных рабочих данных внешнему поставщику.

Подписка ChatGPT в браузере не используется: приложению нужен отдельный API-ключ. Ключ передаётся API в заголовке Bearer и хранится только в локальном secret-файле. Формат ответа ограничивается строгой JSON Schema через `response_format`. См. официальные документы OpenAI: [API authentication](https://developers.openai.com/api/reference/overview) и [Chat Completions / Structured Outputs](https://developers.openai.com/api/reference/resources/chat).

## Что происходит с данными

До первого сетевого запроса во внешний API backend выполняет локальный конвейер:

1. Реальные `source_ref` заменяются временными `SRC-0001`, `SRC-0002` и так далее. `employee_ref`, Jira key и отображаемое имя заменяются служебными маркерами.
2. Отдельная локальная модель `ai-curator-pii-gemma3-4b` в Ollama ищет имена, адреса, телефоны, email, логины и другие идентификаторы.
3. Найденные моделью фрагменты заменяются маркерами. Затем без LLM применяется обязательный словарь и регулярные выражения для email, телефонов и username.
4. Только полученная копия контекста поступает во внешнюю модель — сначала для извлечения событий из комментариев, затем для итогового сценария.
5. После ответа реальные ссылки на источники восстанавливаются локально, и обычный валидатор проверяет схему, даты и допустимые ссылки.

Это fail-closed контур: если PII-модель недоступна, вернула некорректный результат, словарь отсутствует или пуст, внешний запрос не выполняется. При этом автоматическое обезличивание всё равно не даёт математической гарантии удаления любых чувствительных сведений. Используйте режим только после организационной и юридической проверки поставщика, условий хранения данных и состава контекста.

Код внешнего контура изолирован:

- `src/ai_curator/external_llm.py` — внешний OpenAI-compatible клиент и сборка runtime;
- `src/ai_curator/privacy_filter.py` — локальная PII-модель, словарь, regex и псевдонимы источников;
- `prompts/pii_filter.system.txt` — отдельная инструкция PII-модели.

## Однократная подготовка

Установите и запустите native Ollama. Затем скачайте Gemma 3 4B и создайте специальный локальный тег фильтра:

```bash
./scripts/prepare-ollama.sh pii-filter
ollama show ai-curator-pii-gemma3-4b
```

На PowerShell:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-ollama.ps1 pii-filter
ollama show ai-curator-pii-gemma3-4b
```

Обычный `start` модель не скачивает.

## Секрет и словарь

Создайте `secrets/external_llm_api_key`, вставьте туда только API-ключ и ограничьте права файла. Не помещайте ключ в `.env`, исходный код, URL или команды shell.

```bash
chmod 600 secrets/external_llm_api_key
```

Создайте непустой `secrets/pii_dictionary.txt`: одна строка — один фрагмент для регистронезависимой замены. Пустые строки и строки, начинающиеся с `#`, игнорируются. Можно начать с безопасного шаблона:

```bash
cp docs/pii_dictionary.example.txt secrets/pii_dictionary.txt
chmod 600 secrets/pii_dictionary.txt
```

```text
# ФИО, варианты написания, логины и внутренние идентификаторы
Иван Иванов
ivan.ivanov
Команда Секретного Проекта
```

Добавляйте варианты склонения и написания отдельными строками. Имя и Jira key выбранного сотрудника также автоматически добавляются во временный словарь текущего запроса.

## Включение

Измените `.env`:

```dotenv
CURATOR_LLM_RUNTIME=external
CURATOR_EXTERNAL_LLM_ENABLED=true
CURATOR_EXTERNAL_LLM_BASE_URL=https://api.openai.com/v1
CURATOR_EXTERNAL_LLM_MODEL=gpt-4.1-mini
CURATOR_EXTERNAL_LLM_API_KEY_FILE=/secrets/external_llm_api_key

CURATOR_PII_FILTER_BASE_URL=http://host.docker.internal:11434
CURATOR_PII_FILTER_MODEL=ai-curator-pii-gemma3-4b
CURATOR_PII_FILTER_DICTIONARY_FILE=/secrets/pii_dictionary.txt
```

Запустите:

```bash
./scripts/start.sh
docker compose logs app --tail 100
```

В «Модель и защита данных» должны одновременно быть доступны внешняя модель и `privacy_filter`. Если локальный фильтр unhealthy, генерация заблокирована до исправления.

## Возврат к локальной модели

Например, для Qwen 8B:

```dotenv
CURATOR_LLM_RUNTIME=ollama
CURATOR_OLLAMA_MODEL=ai-curator-qwen3-8b
CURATOR_EXTERNAL_LLM_ENABLED=false
```

После изменения повторите `./scripts/start.sh`. Для Docker `llama.cpp` установите `CURATOR_LLM_RUNTIME=llama_cpp` и также верните opt-in в `false`.

## Логи

При `CURATOR_LLM_LOG_REQUEST_RESPONSE=true` внешний клиент логирует только уже обезличенный запрос и ответ событиями `external_llm_request_payload` и `external_llm_response_payload`. Сырой запрос локальной PII-модели намеренно не логируется даже при этом флаге. Итог `pii_filter_completed` содержит только время, число проверенных текстов и число замен.

После диагностики верните флаг в `false`: обезличенный контекст и ответ всё равно могут содержать внутреннюю рабочую информацию.
