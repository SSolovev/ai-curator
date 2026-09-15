# Запуск AI Curator с Native Ollama: Gemma 4 12B и Qwen3 8B

Это основной сценарий для macOS с Apple Silicon. Приложение AI Curator работает в Docker, а основная Gemma 4 12B или более лёгкая Qwen3 8B — в установленной на компьютере Ollama с Metal. Docker-контейнер `llama.cpp` в этом режиме не запускается.

## Что понадобится

- Docker Desktop;
- [Native Ollama](https://docs.ollama.com/quickstart);
- 16 ГБ RAM достаточно для запуска одной из моделей, 32 ГБ даёт больший запас;
- около 10 ГБ свободного места для Qwen либо 14 ГБ для Gemma вместе с Docker-образом;
- файл проекта AI Curator.

Все команды ниже выполняются из корневого каталога проекта.

## 1. Запустите Ollama

Установите и откройте приложение Ollama. Затем проверьте его в Terminal:

```bash
ollama --version
curl http://127.0.0.1:11434/api/tags
```

Если второй запрос не работает и приложение Ollama не запущено, выполните в отдельном окне Terminal:

```bash
ollama serve
```

Не закрывайте это окно до завершения работы с AI Curator.

## 2. Подготовьте `.env`

Создайте `.env`, только если его ещё нет:

```bash
test -f .env || cp .env.example .env
```

Не копируйте `.env.example` поверх существующего `.env`: так можно потерять локальные настройки, включая включённые диагностические логи.

Проверьте следующие строки в `.env`:

```dotenv
CURATOR_LLM_RUNTIME=ollama
CURATOR_OLLAMA_BASE_URL=http://host.docker.internal:11434
# Основная модель:
CURATOR_OLLAMA_MODEL=ai-curator-gemma4-12b
CURATOR_LLM_TEMPERATURE=0.2
CURATOR_LLM_REASONING_EFFORT=low
```

Контекст задаётся в Modelfile: Qwen использует 32K, а Gemma 4 — 16K. Gemma 4 поддерживает thinking, но по умолчанию приложение передаёт `reasoning_effort=none`: профиль `low` может потратить весь лимит ответа на reasoning и не сформировать JSON. Моделям Gemma 3 этот параметр не отправляется.

```text
models/Modelfile.qwen3-8b:    PARAMETER num_ctx 32768
models/Modelfile.gemma4-12b:  PARAMETER num_ctx 16384
```

## 3. Подготовьте источник модели

Для Qwen в каталоге `models` должен находиться файл:

```text
models/Qwen3-8B-Q4_K_M.gguf
```

Проверка:

```bash
ls -lh models/Qwen3-8B-Q4_K_M.gguf
```

Если файла нет, скачайте официальный `Qwen3-8B-Q4_K_M.gguf` размером 5,03 ГБ со страницы [Qwen/Qwen3-8B-GGUF](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/1d54a16a18cba0d8fbad4a16db801decc729e099/Qwen3-8B-Q4_K_M.gguf) и положите его в `models` под указанным именем.

Ожидаемый SHA-256:

```text
d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785
```

Проверка на macOS:

```bash
shasum -a 256 models/Qwen3-8B-Q4_K_M.gguf
```

Для Gemma локальный GGUF вручную не нужен. Скрипт выполнит `ollama pull gemma4:12b` из официального каталога Ollama. Это Q4_K_M-пакет размером около 7,6 ГБ. Загрузка выполняется только при подготовке, а не при каждом старте приложения.

## 4. Однократно создайте локальный HMAC-ключ

Этот ключ используется для псевдонимизации Jira employee key. Он не является Jira PAT.

```bash
mkdir -p secrets certs
test -s secrets/hmac_key || openssl rand -hex 32 > secrets/hmac_key
chmod 600 secrets/hmac_key
```

Jira PAT в этот файл записывать нельзя. PAT безопаснее вводить в web-интерфейсе приложения.

## 5. Создайте локальный тег Ollama

Для Qwen3 8B:

```bash
./scripts/prepare-ollama.sh qwen3-8b
```

Для Gemma 4 12B:

```bash
./scripts/prepare-ollama.sh gemma4-12b
```

Для подготовки обеих моделей:

```bash
./scripts/prepare-ollama.sh all
```

Без аргумента скрипт подготавливает основную Gemma 4 12B. Он создаёт локальные теги `ai-curator-gemma4-12b` и/или `ai-curator-qwen3-8b` и применяет параметры соответствующего Modelfile. Проверьте результат:

```bash
ollama show ai-curator-gemma4-12b
ollama show ai-curator-qwen3-8b
```

После изменения Modelfile снова выполните команду подготовки соответствующей модели: только так обновлённые параметры попадут в локальный тег.

## 6. Выберите модель и запустите AI Curator

Gemma 4 12B — основной вариант с более сильным reasoning и structured output:

```dotenv
CURATOR_LLM_RUNTIME=ollama
CURATOR_OLLAMA_MODEL=ai-curator-gemma4-12b
```

Qwen3 8B — более быстрый и экономный вариант:

```dotenv
CURATOR_LLM_RUNTIME=ollama
CURATOR_OLLAMA_MODEL=ai-curator-qwen3-8b
```

После изменения `.env` выполните:

```bash
./scripts/start.sh
```

После сообщения об успешном запуске откройте:

<http://127.0.0.1:8080>

В блоке «Локальная модель» должно быть указано:

```text
Активный runtime: Ollama (Native)
Готова: ai-curator-gemma4-12b
```

Для Qwen строка состояния содержит `ai-curator-qwen3-8b`.

## Переключение между Qwen и Gemma

Модели достаточно подготовить по одному разу. Для переключения:

1. Измените `CURATOR_OLLAMA_MODEL` в `.env`.
2. Выполните `./scripts/start.sh`.
3. Откройте диагностику внизу страницы и проверьте точное имя модели.

Повторный `ollama pull` или `prepare-ollama.sh` для обычного переключения не требуется. `start.sh` остановит ранее выбранную модель, чтобы две крупные модели не занимали память одновременно.

Первый запрос может быть медленнее последующих: Ollama загружает модель в объединённую память компьютера. `ollama ps` может ничего не показывать до первой генерации — это нормально.

`scripts/start.sh` автоматически останавливает Docker-модель `llama.cpp`. Одновременно AI Curator использует только один runtime.

## Повторный запуск и остановка

Для следующего запуска:

```bash
./scripts/start.sh
```

Для полной остановки приложения и выгрузки модели AI Curator из Ollama:

```bash
./scripts/stop.sh
```

## Быстрая диагностика

Проверить Ollama:

```bash
curl http://127.0.0.1:11434/api/tags
ollama show ai-curator-gemma4-12b
ollama show ai-curator-qwen3-8b
```

Проверить приложение:

```bash
curl http://127.0.0.1:8080/healthz
docker compose logs app --tail 100
```

Если `start.sh` сообщает, что модель не найдена, выполните `./scripts/prepare-ollama.sh gemma4-12b` или `./scripts/prepare-ollama.sh qwen3-8b` в соответствии с выбранным тегом. Если контейнер приложения не видит Ollama, сначала убедитесь, что локальный запрос к порту `11434` работает и Ollama продолжает выполняться.

Диагностические Jira- и LLM-логи могут содержать рабочие и персональные данные. Не публикуйте их и не отправляйте во внешние сервисы без очистки. Jira PAT и заголовок `Authorization` приложение в логи не записывает.

## Windows PowerShell

На Windows используются те же файлы конфигурации и те же значения `.env`. Команды запуска отличаются:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-ollama.ps1 gemma4-12b
# либо
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-ollama.ps1 qwen3-8b
PowerShell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Перед этим установите и запустите [Ollama for Windows](https://docs.ollama.com/windows) и создайте `secrets/hmac_key`. Локальный GGUF нужен только для Qwen; Gemma скачивается командой подготовки через Ollama. Для остановки используйте `scripts/stop.ps1`.
