# Безопасность и приватность

## Допустимое использование

ИИ-Куратор помогает подготовить разговор. Он не диагностирует человека, не рассчитывает вероятность ухода и не рекомендует увольнение, повышение, понижение, компенсацию или иное значимое кадровое решение. Человеческая запись `jira_recorded_risk` показывается именно как запись Jira, не как AI-прогноз. Любой вывод нужно проверить по датированным источникам и в разговоре.

## Threat model

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Кража Jira PAT | поле password; только RAM или read-only secret-file; Bearer только header; no access logs; allowlisted JSON logs | malware/root на ноутбуке видит process memory |
| Prompt injection в комментарии | Jira-текст только в user JSON между XML-границами; system priority; нет tools у модели; source allowlist; constrained generation format | backend не фильтрует формулировки ответа; требуется human review |
| JQL/path injection | строгий `^[A-Z][A-Z0-9]{1,19}-[1-9][0-9]{0,9}$`; search text фильтруется локально; query parameters | нестандартные Jira project keys потребуют осознанного изменения regex |
| MITM Jira | TLS verify всегда включён; corporate CA bundle; no redirect | компрометация корпоративного CA вне контроля приложения |
| Случайная публикация UI | host binding `127.0.0.1`; Trusted Host, CSP, CSRF, SameSite cookie; model port не опубликован | другой локальный процесс может обратиться к UI; ОС/endpoint protection остаются частью boundary |
| SSRF через base URL | URL валидируется, credentials/query запрещены, HTTPS обязателен кроме mock; Jira destination вводит пользователь | DNS rebinding/корпоративный DNS контролируются инфраструктурой |
| PII в логах | по умолчанию только event/code/correlation/duration; диагностический LLM payload содержит уже отфильтрованный точный запрос основной модели; сырой PII-ввод не логируется | фильтр может пропустить косвенный идентификатор; диагностические логи требуют ограниченного доступа |
| PII в основной AI-модели | для всех runtime локальная PII-модель, обязательный словарь, regex, source aliases и fail-closed; для внешнего API дополнительный явный opt-in | автоматическое обнаружение может пропустить косвенный идентификатор; для external нужны организационное разрешение и проверка поставщика |
| Persisted PII | нет БД/caches/prompts; export только по действию; field mapping зафиксирован в коде | browser/download history и выбранный каталог экспорта контролирует пользователь |
| Supply-chain compromise | точные Python versions; base/runtime image digests; model URL commit + SHA-256; SBOM/CVE scripts | digest нужно обновлять вместе с security fixes после проверки |
| Resource exhaustion | response/comment/context limits; max pages; one concurrent generation; timeout/cancel | длинная CPU generation всё ещё нагружает ноутбук |

## Секреты

- Никогда не добавляйте PAT в `.env`, Compose, URL, issue key, комментарий, prompt или screenshot.
- Безопасный default: ввод в UI, затем «Завершить сессию». PAT исчезает при удалении сессии/перезапуске.
- Docker secret-file: `secrets/jira_token`, одна строка, права владельца. Каталог исключён из Git. На общем компьютере предпочтителен ввод в UI.
- HMAC key `secrets/hmac_key` генерируется случайно при подготовке модели. Его ротация изменит псевдоним `employee_ref`; данные не потеряются, потому что история не хранится.
- API-ключ внешней LLM хранится только в `secrets/external_llm_api_key`. Он не передаётся PII-модели, не записывается в `.env` и не логируется.
- Desktop keychain не реализован: MVP поставляется как Docker web app. При создании native desktop wrapper использовать Windows Credential Manager/macOS Keychain/libsecret.

## Сетевой контур

В локальных runtime приложению нужны Jira, выбранный model service и локальная Ollama PII-модель. Оно не содержит telemetry/CDN. Docker bridge сам по себе не является egress firewall: в строгом контуре примените корпоративный host firewall/proxy allowlist для Jira host и локального Ollama endpoint; контейнеру `model` внешний egress не нужен. Runtime `external` осознанно добавляет HTTPS egress только к настроенному AI API; ограничьте allowlist этим host и примените требования вашей организации к внешней обработке данных.

## Retention и безопасное удаление

Обычная остановка: `scripts/stop.sh` / `scripts/stop.ps1`. Для удаления session RAM нажмите «Завершить сессию» или остановите контейнер.

Экспорт удалите средствами ОС с учётом корпоративной политики. Полное локальное удаление после проверки текущего каталога:

```bash
docker compose --profile llama down --volumes
```

Затем вручную удалите только `models/Qwen3-4B-Q4_K_M.gguf`, `secrets/hmac_key`, `secrets/jira_token` и собственные exports. Эти операции необратимы; приложение намеренно не автоматизирует их.

## Security checks

`./scripts/security-report.sh` формирует inventory и, если локально установлены `pip-audit`, `syft`, `grype`, CVE/SBOM reports. Отсутствующий scanner отмечается `NOT_RUN`, а не выдаётся за успешную проверку.
