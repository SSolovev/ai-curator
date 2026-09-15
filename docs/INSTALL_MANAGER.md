# Установка для менеджера

Эта инструкция не требует редактировать код. Основной вариант — Windows 11 + WSL2 + Docker Desktop. На macOS/Linux команды в Terminal те же, что в блоках Bash.

## 1. Проверьте компьютер

Нужно:

- 32 ГБ системной RAM (это не означает 32 ГБ видеопамяти);
- минимум 12 ГБ свободного диска для default: модель 5.03 ГБ, container images/кэш около 2–4 ГБ и запас;
- Docker Desktop с WSL2 engine на Windows, либо Docker Engine/Podman Compose на Linux;
- во время первой подготовки — доступ к официальному Hugging Face; после этого интернет модели не нужен;
- доступ по VPN/сети к вашей Jira.

В PowerShell: `docker version`, `docker compose version`. В WSL/Linux/macOS: `free -h` (Linux) или Activity Monitor (macOS), `df -h .`.

## 2. Однократно подготовьте модель

PowerShell из каталога проекта:

```powershell
Copy-Item .env.example .env
PowerShell -ExecutionPolicy Bypass -File scripts/prepare-model.ps1
```

WSL/Linux/macOS:

```bash
cp .env.example .env
./scripts/prepare-model.sh
```

Скачивается ровно `Qwen3-4B-Q4_K_M.gguf`, 5.03 GB, с official Qwen repository на зафиксированном commit. Скрипт сверяет SHA-256. Повторный запуск не скачивает корректный файл заново. Веса не находятся внутри app image.

## 3. Корпоративный сертификат и PAT

Если Jira использует корпоративный CA, положите выданный IT-службой public PEM bundle в `certs/`, например `certs/company-ca.pem`. В UI укажите контейнерный путь `/certs/company-ca.pem`. Не выключайте TLS verification.

Безопасный default — ввести PAT в web-форме: он останется только в RAM до конца сессии. Не вставляйте PAT в `.env`, URL или Jira comment.

Если политика требует secret-file, создайте одну строку в `secrets/jira_token`; ограничьте права владельца. Этот файл не входит в Git/offline bundle.

## 4. Запустите

PowerShell: `PowerShell -ExecutionPolicy Bypass -File scripts/start.ps1`.

WSL/Linux/macOS: `./scripts/start.sh`.

Первый build занимает несколько минут. Когда containers станут healthy, откройте <http://127.0.0.1:8080>. Приложение не должно открываться по IP ноутбука из сети.

На macOS с Apple Silicon можно использовать native Ollama с Metal: основная модель — Gemma 4 12B Q4_K_M с контекстом 16K, более лёгкая альтернатива — Qwen3 8B. Полная пошаговая инструкция, включая подготовку модели, переключение через `.env`, HMAC-ключ и диагностику: [OLLAMA_MODELS_QUICKSTART.md](OLLAMA_MODELS_QUICKSTART.md).

Кратко: установите и запустите Ollama, затем подготовьте нужную модель:

```bash
./scripts/prepare-ollama.sh gemma4-12b
# либо более лёгкая модель
./scripts/prepare-ollama.sh qwen3-8b
```

В `.env` установите `CURATOR_LLM_RUNTIME=ollama` и `CURATOR_OLLAMA_MODEL=ai-curator-gemma4-12b` либо `ai-curator-qwen3-8b`, затем повторите `./scripts/start.sh`; для Docker используйте `llama_cpp`. Одновременно запускается только выбранная модель: start-скрипт останавливает `llama.cpp` при выборе Ollama и выгружает невыбранные модели Ollama.

Опциональный внешний API настраивается только после согласования обработки данных. Для него нужны отдельный API-key secret, словарь и локальная PII-модель; используйте [EXTERNAL_LLM.md](EXTERNAL_LLM.md), не переносите ключ в `.env`.

## 5. Подключитесь к Jira

1. Выберите источник: рабочая Jira `https://project.gpb.tech/jira`, демо с вымышленными данными или другой адрес Jira. Для Jira с context path указывается база до `/browse`, без ключа задачи и без `/rest/api/...`.
2. Для другого адреса введите base URL вида `https://jira.company.local`.
3. Укажите `/certs/company-ca.pem` и proxy, если это требует IT.
4. Для рабочей или своей Jira введите PAT и нажмите «Проверить подключение». Приложение вызовет read-only `GET /rest/api/2/myself` и не покажет сам token. Для демо адрес и тестовый токен подставляются автоматически.
5. При успехе найдите подопечного или введите ключ вида `TEAM-123`.
6. Приложение сразу загрузит контекст, используя встроенный подтверждённый mapping полей.

## 6. Создайте сценарий

На экране контекста прочитайте каждую запись. Снимите checkbox с лишнего. Не включайте отображаемое имя без необходимости. Нажмите «Подготовить сценарий»; кнопку «Отменить» можно использовать во время generation.

Результат появится в большом редактируемом поле. Подправьте формулировки и нажмите «Копировать текст», чтобы перенести именно отредактированную версию в документ или комментарий Jira. Файл `.md` также содержит ваши правки; исходный JSON остаётся отдельным техническим экспортом. Ниже поля можно раскрыть структурированный результат, проверить источники и даты, отделить факты от гипотез. Скачивание Markdown/JSON — явное сохранение на ваш диск; дальше файл защищается вашей корпоративной политикой.

AI-вывод нельзя использовать как автоматическое основание для кадрового решения, психологический диагноз или прогноз ухода.

## 7. Остановка, обновление, диагностика

- остановить: `scripts/stop.ps1` или `./scripts/stop.sh`;
- проверить app: <http://127.0.0.1:8080/healthz>;
- внизу UI открыть «Модель и защита данных» и выполнить диагностику активного runtime — в диагностическом ответе нет персональных данных;
- обновить только из проверенного release: `scripts/update.ps1` в PowerShell или `./scripts/update.sh` в WSL/Linux/macOS, затем снова выполнить smoke/tests;
- логи: `docker compose logs app --tail 100`; они содержат только event/code/correlation/time, но всё равно не публикуйте их без проверки.

## 8. Безопасное удаление

Нажмите «Завершить сессию и забыть данные», затем остановите. Веса модели, HMAC key, optional PAT и созданные вами exports удаляйте вручную только после проверки точного файла. См. [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).

## Offline bundle

На подключённой staging machine выполните `./scripts/prepare-offline-bundle.sh`. Получится каталог примерно 7–10 ГБ (точный `images.tar` зависит от платформы): model 5.03 GB, checksums, app/runtime images и config. Перед переносом security team должна проверить SBOM/CVE report и разрешение Apache-2.0/корпоративную политику распространения весов. В изолированном контуре запустите `install-offline.sh`.

Podman: используйте `podman compose` вместо `docker compose`; health/dependency semantics зависят от версии, а scripts потребуют замены команды. GPU passthrough нужно проверять отдельно.
