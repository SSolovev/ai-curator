# Установка для менеджера

Эта инструкция не требует редактировать код. Основной вариант — Windows 11 + WSL2 + Docker Desktop. На macOS/Linux команды в Terminal те же, что в блоках Bash.

## 1. Проверьте компьютер

Нужно:

- 32 ГБ системной RAM (это не означает 32 ГБ видеопамяти);
- минимум 16 ГБ свободного диска для основной и обязательной PII-модели, container images/кэша и запаса;
- Docker Desktop с WSL2 engine на Windows, либо Docker Engine/Podman Compose на Linux;
- native Ollama для обязательного локального PII-фильтра при любом runtime;
- доступ к источникам весов при первой подготовке локальных моделей и к API провайдера при каждом вызове внешней модели;
- доступ по VPN/сети к вашей Jira.

В PowerShell: `docker version`, `docker compose version`. В WSL/Linux/macOS: `free -h` (Linux) или Activity Monitor (macOS), `df -h .`.

## 2. Подготовьте ключ и фильтр для всех режимов

PowerShell из каталога проекта:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force -Path secrets,certs | Out-Null
if (-not (Test-Path secrets/hmac_key) -or (Get-Item secrets/hmac_key).Length -eq 0) {
    $Bytes = New-Object byte[] 32
    $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Random.GetBytes($Bytes) } finally { $Random.Dispose() }
    [BitConverter]::ToString($Bytes).Replace("-", "").ToLowerInvariant() |
        Set-Content -Path secrets/hmac_key -Encoding ascii -NoNewline
}
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-ollama.ps1 pii-filter
if (-not (Test-Path secrets/pii_dictionary.txt)) { Copy-Item docs/pii_dictionary.example.txt secrets/pii_dictionary.txt }
```

WSL/Linux/macOS:

```bash
test -f .env || cp .env.example .env
mkdir -p secrets certs
(umask 077; test -s secrets/hmac_key || openssl rand -hex 32 > secrets/hmac_key)
./scripts/prepare-ollama.sh pii-filter
test -f secrets/pii_dictionary.txt || cp docs/pii_dictionary.example.txt secrets/pii_dictionary.txt
chmod 600 secrets/pii_dictionary.txt
```

Команды создают HMAC-ключ и подготавливают тег `ai-curator-pii-gemma3-4b`. Если в существующем `.env` выбран другой тег для `CURATOR_PII_FILTER_MODEL`, проверьте его командой `ollama show <тег>` либо переключитесь на подготовленный тег. Перед подключением рабочей Jira заполните `secrets/pii_dictionary.txt` организационными именами, логинами и идентификаторами. Пустой словарь или недоступность фильтра блокируют вызов основной модели.

Если скрипт сообщает, что отсутствует Modelfile фильтра, восстановите его по [инструкции Ollama](OLLAMA_MODELS_QUICKSTART.md#5-создайте-локальные-теги-ollama-и-pii-словарь).

## 3. Корпоративный сертификат и PAT

Если Jira использует корпоративный CA, положите выданный IT-службой public PEM bundle в `certs/`, например `certs/company-ca.pem`. В UI укажите контейнерный путь `/certs/company-ca.pem`. Не выключайте TLS verification.

Безопасный default — ввести PAT в web-форме: он останется только в RAM до конца сессии. Не вставляйте PAT в `.env`, URL или Jira comment.

Если политика требует secret-file, создайте одну строку в `secrets/jira_token`; ограничьте права владельца. Этот файл не входит в Git/offline bundle.

## 4. Подготовьте основную модель и запустите приложение

Выполните подготовку из корня проекта и выберите один режим в существующем `.env`.

### Локальная Qwen3 4B в Docker

Выполните `./scripts/prepare-model.sh` на macOS/Linux или `PowerShell -ExecutionPolicy Bypass -File scripts/prepare-model.ps1` в PowerShell. Скрипт скачает около 2,5 ГБ в `models/Qwen3-4B-Q4_K_M.gguf` и сверит SHA-256 с `models/SHA256SUMS`. Укажите в `.env` следующие значения для запуска на CPU.

```dotenv
CURATOR_LLM_RUNTIME=llama_cpp
LLAMA_GPU_LAYERS=0
```

### Локальная Gemma 4 12B или Qwen3 8B в Ollama

- Для Gemma 4 12B выполните `./scripts/prepare-ollama.sh gemma4-12b`. Скрипт скачает веса из каталога Ollama и создаст тег `ai-curator-gemma4-12b`.
- Для Qwen3 8B скачайте [официальный файл GGUF](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/1d54a16a18cba0d8fbad4a16db801decc729e099/Qwen3-8B-Q4_K_M.gguf) в `models/Qwen3-8B-Q4_K_M.gguf`, сверив контрольную сумму. Затем выполните `./scripts/prepare-ollama.sh qwen3-8b`, чтобы создать тег `ai-curator-qwen3-8b`. [Контрольная сумма и настройка Ollama](OLLAMA_MODELS_QUICKSTART.md)

В PowerShell используйте `PowerShell -ExecutionPolicy Bypass -File scripts/prepare-ollama.ps1 gemma4-12b` или замените последний аргумент на `qwen3-8b`. Проверьте установленные теги через `ollama list`. Пример настроек для Gemma 4 12B приведён ниже. Для Qwen замените значение `CURATOR_OLLAMA_MODEL`.

```dotenv
CURATOR_LLM_RUNTIME=ollama
CURATOR_OLLAMA_BASE_URL=http://host.docker.internal:11434
CURATOR_OLLAMA_MODEL=ai-curator-gemma4-12b
```

### Внешняя модель через API

Провайдер запускает модель на своей стороне. Получите базовый HTTPS-адрес, ID модели и API-ключ. Включите `CURATOR_LLM_RUNTIME=external` и `CURATOR_EXTERNAL_LLM_ENABLED=true`, заполните `CURATOR_EXTERNAL_LLM_BASE_URL` и `CURATOR_EXTERNAL_LLM_MODEL`. Секретную часть ключа сохраните в `secrets/external_llm_api_key`. Локальный фильтр из шага 2 нужен и для этого режима. [Настройки и пример для Yandex AI Studio](EXTERNAL_LLM.md)

### Запуск и проверка

- macOS/Linux/WSL — `./scripts/start.sh`.
- PowerShell — `PowerShell -ExecutionPolicy Bypass -File scripts/start.ps1`.

Первое создание контейнеров может занять несколько минут. Откройте <http://127.0.0.1:8080> или порт из `CURATOR_PORT`. В блоке «Модель и защита данных» нажмите «Проверить модель» у основной модели и у модели обезличивания. Затем сформируйте сценарий в демо Jira.

Повторите стартовый скрипт после смены режима, тега или параметров в `.env`. Открытая сессия и результаты в памяти будут потеряны при перезапуске. Внешний режим включайте после согласования обработки данных.

## 5. Подключитесь к Jira

1. Выберите источник: рабочая Jira `https://project.gpb.tech/jira`, демо с вымышленными данными или другой адрес Jira. Для Jira с context path указывается база до `/browse`, без ключа задачи и без `/rest/api/...`.
2. Для другого адреса введите base URL вида `https://jira.company.local`.
3. Укажите `/certs/company-ca.pem` и proxy, если это требует IT.
4. Для рабочей или своей Jira введите PAT и нажмите «Проверить подключение». Приложение вызовет read-only `GET /rest/api/2/myself` и не покажет сам token. Для демо адрес и тестовый токен подставляются автоматически.
5. При успехе найдите подопечного или введите ключ вида `TEAM-123`.
6. Приложение сразу загрузит контекст, используя встроенный подтверждённый mapping полей.

## 6. Создайте сценарий

На экране контекста проверьте записи и снимите флажки с тех, которые хотите исключить. Затем нажмите «Подготовить сценарий». Имя автоматически скрывается перед запросом к основной модели. Пока сценарий формируется, доступна кнопка «Отменить».

Результат появится в редактируемом поле. Проверьте источники и подправьте формулировки. Кнопка «Опубликовать в Jira» показывает ключ исходной задачи и добавляет в неё текущий текст с пометкой `!СГЕНЕРИРОВАНО ИИ!`. Для публикации нужна учётная запись с правом добавлять комментарии в эту задачу. Кнопка «Копировать текст» и файл `.md` также используют текущий текст редактора. Исходный JSON содержит структурированный результат генерации.

AI-вывод нельзя использовать как автоматическое основание для кадрового решения, психологический диагноз или прогноз ухода.

## 7. Остановка, обновление, диагностика

- остановить: `scripts/stop.ps1` или `./scripts/stop.sh`;
- проверить app: <http://127.0.0.1:8080/healthz>;
- внизу страницы откройте «Модель и защита данных». У основной модели и модели обезличивания есть отдельные кнопки проверки и статусы;
- обновить только из проверенного release: `scripts/update.ps1` в PowerShell или `./scripts/update.sh` в WSL/Linux/macOS, затем снова выполнить smoke/tests;
- последние логи приложения — `docker compose logs --tail 100 app`;
- просмотр логов в реальном времени, журналы Ollama и диагностика по кодам ошибок — [руководство по диагностике](TROUBLESHOOTING.md#где-смотреть-логи).

## 8. Безопасное удаление

Нажмите «Завершить сессию и забыть данные», затем остановите. Веса модели, HMAC key, optional PAT и созданные вами exports удаляйте вручную только после проверки точного файла. См. [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).

## Offline bundle

Скрипт `prepare-offline-bundle.sh` упаковывает Compose, основную Docker-модель и образы. Для полностью изолированного запуска отдельно подготовьте Ollama и выбранный тег фильтра на целевом компьютере. Положите непустой словарь в `secrets/` распакованного комплекта до запуска установщика. `install-offline.sh` копирует `.env.example` поверх `.env` и сразу запускает контейнеры, поэтому используйте отдельный каталог. Демо Jira требует исходники и фикстуры, которые в комплект не входят.

Podman: используйте `podman compose` вместо `docker compose`; health/dependency semantics зависят от версии, а scripts потребуют замены команды. GPU passthrough нужно проверять отдельно.
