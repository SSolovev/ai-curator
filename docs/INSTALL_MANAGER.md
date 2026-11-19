# Установка для менеджера

Эта инструкция не требует редактировать код. Основной вариант — Windows 11 + WSL2 + Docker Desktop. На macOS/Linux команды в Terminal те же, что в блоках Bash.

## 1. Проверьте компьютер

Нужно:

- 32 ГБ системной RAM (это не означает 32 ГБ видеопамяти);
- минимум 16 ГБ свободного диска для основной и обязательной PII-модели, container images/кэша и запаса;
- Docker Desktop с WSL2 engine на Windows, либо Docker Engine/Podman Compose на Linux;
- native Ollama для обязательного локального PII-фильтра при любом runtime;
- во время первой подготовки — доступ к официальным источникам моделей; после этого интернет моделям не нужен;
- доступ по VPN/сети к вашей Jira.

В PowerShell: `docker version`, `docker compose version`. В WSL/Linux/macOS: `free -h` (Linux) или Activity Monitor (macOS), `df -h .`.

## 2. Однократно подготовьте модель

PowerShell из каталога проекта:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-model.ps1
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-ollama.ps1 pii-filter
if (-not (Test-Path secrets/pii_dictionary.txt)) { Copy-Item docs/pii_dictionary.example.txt secrets/pii_dictionary.txt }
```

WSL/Linux/macOS:

```bash
test -f .env || cp .env.example .env
./scripts/prepare-model.sh
./scripts/prepare-ollama.sh pii-filter
test -f secrets/pii_dictionary.txt || cp docs/pii_dictionary.example.txt secrets/pii_dictionary.txt
chmod 600 secrets/pii_dictionary.txt
```

Основная Docker-модель и специализированная PII-модель готовятся один раз. Перед production заполните `secrets/pii_dictionary.txt` реальными организационными именами, логинами и идентификаторами. Пустой словарь, недоступная PII-модель или Ollama блокируют вызов основной модели.

## 3. Корпоративный сертификат и PAT

Если Jira использует корпоративный CA, положите выданный IT-службой public PEM bundle в `certs/`, например `certs/company-ca.pem`. В UI укажите контейнерный путь `/certs/company-ca.pem`. Не выключайте TLS verification.

Безопасный default — ввести PAT в web-форме: он останется только в RAM до конца сессии. Не вставляйте PAT в `.env`, URL или Jira comment.

Если политика требует secret-file, создайте одну строку в `secrets/jira_token`; ограничьте права владельца. Этот файл не входит в Git/offline bundle.

## 4. Запустите

Для основной модели в Docker проверьте, что в `.env` указано `CURATOR_LLM_RUNTIME=llama_cpp`.

PowerShell: `PowerShell -ExecutionPolicy Bypass -File scripts/start.ps1`.

WSL/Linux/macOS: `./scripts/start.sh`.

Первый build занимает несколько минут. Когда containers станут healthy, откройте <http://127.0.0.1:8080>. Приложение не должно открываться по IP ноутбука из сети.

На macOS с Apple Silicon можно использовать native Ollama с Metal: основная модель — Gemma 4 12B Q4_K_M с контекстом 16K, более лёгкая альтернатива — Qwen3 8B. Полная пошаговая инструкция, включая подготовку модели, переключение через `.env`, HMAC-ключ и диагностику: [OLLAMA_MODELS_QUICKSTART.md](OLLAMA_MODELS_QUICKSTART.md).

Кратко: установите и запустите Ollama, подготовьте обязательный фильтр и нужную основную модель:

```bash
./scripts/prepare-ollama.sh pii-filter
./scripts/prepare-ollama.sh gemma4-12b
# либо более лёгкая модель
./scripts/prepare-ollama.sh qwen3-8b
```

В `.env` установите `CURATOR_LLM_RUNTIME=ollama` и `CURATOR_OLLAMA_MODEL=ai-curator-gemma4-12b` либо `ai-curator-qwen3-8b`, затем повторите `./scripts/start.sh`; для Docker используйте `llama_cpp`. Одновременно запускается только выбранная модель: start-скрипт останавливает `llama.cpp` при выборе Ollama и выгружает невыбранные модели Ollama.

Опциональный внешний API настраивается только после согласования обработки данных. Общий PII-фильтр и словарь уже обязательны для всех runtime; для external дополнительно нужен API-key secret. Используйте [EXTERNAL_LLM.md](EXTERNAL_LLM.md), не переносите ключ в `.env`.

## 5. Подключитесь к Jira

1. Выберите источник: рабочая Jira `https://project.gpb.tech/jira`, демо с вымышленными данными или другой адрес Jira. Для Jira с context path указывается база до `/browse`, без ключа задачи и без `/rest/api/...`.
2. Для другого адреса введите base URL вида `https://jira.company.local`.
3. Укажите `/certs/company-ca.pem` и proxy, если это требует IT.
4. Для рабочей или своей Jira введите PAT и нажмите «Проверить подключение». Приложение вызовет read-only `GET /rest/api/2/myself` и не покажет сам token. Для демо адрес и тестовый токен подставляются автоматически.
5. При успехе найдите подопечного или введите ключ вида `TEAM-123`.
6. Приложение сразу загрузит контекст, используя встроенный подтверждённый mapping полей.

## 6. Создайте сценарий

На экране контекста проверьте записи и снимите флажки с тех, которые хотите исключить. Затем нажмите «Подготовить сценарий». Имя автоматически скрывается перед запросом к основной модели. Пока сценарий формируется, доступна кнопка «Отменить».

Результат появится в большом редактируемом поле. Подправьте формулировки и нажмите «Копировать текст», чтобы перенести именно отредактированную версию в документ или комментарий Jira. Файл `.md` также содержит ваши правки; исходный JSON остаётся отдельным техническим экспортом. Ниже поля можно раскрыть структурированный результат, проверить источники и даты, отделить факты от гипотез. Скачивание Markdown/JSON — явное сохранение на ваш диск; дальше файл защищается вашей корпоративной политикой.

AI-вывод нельзя использовать как автоматическое основание для кадрового решения, психологический диагноз или прогноз ухода.

## 7. Остановка, обновление, диагностика

- остановить: `scripts/stop.ps1` или `./scripts/stop.sh`;
- проверить app: <http://127.0.0.1:8080/healthz>;
- внизу страницы откройте «Модель и защита данных». У основной модели и модели обезличивания есть отдельные кнопки проверки и статусы;
- обновить только из проверенного release: `scripts/update.ps1` в PowerShell или `./scripts/update.sh` в WSL/Linux/macOS, затем снова выполнить smoke/tests;
- логи: `docker compose logs app --tail 100`; по умолчанию они содержат только event/code/correlation/time. При `CURATOR_LLM_LOG_REQUEST_RESPONSE=true` в них находится точный отфильтрованный payload основной модели, поэтому не публикуйте их без проверки.

## 8. Безопасное удаление

Нажмите «Завершить сессию и забыть данные», затем остановите. Веса модели, HMAC key, optional PAT и созданные вами exports удаляйте вручную только после проверки точного файла. См. [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).

## Offline bundle

Текущий `prepare-offline-bundle.sh` упаковывает основную Docker-модель и образы, но не экспортирует native Ollama PII-модель. Поэтому полностью изолированная установка дополнительно требует одобренного вашей организацией offline-переноса Ollama-модели `ai-curator-pii-gemma3-4b`; без неё приложение fail-closed и не выполняет генерацию. Перед переносом security team должна проверить веса, SBOM/CVE report и лицензии.

Podman: используйте `podman compose` вместо `docker compose`; health/dependency semantics зависят от версии, а scripts потребуют замены команды. GPU passthrough нужно проверять отдельно.
