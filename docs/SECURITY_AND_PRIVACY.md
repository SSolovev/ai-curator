# Безопасность и приватность

## Допустимое использование

ИИ-Куратор помогает подготовить разговор. Он не диагностирует человека, не рассчитывает вероятность ухода и не рекомендует увольнение, повышение, понижение, компенсацию или иное значимое кадровое решение. Человеческая запись `jira_recorded_risk` показывается именно как запись Jira, не как AI-прогноз. Любой вывод нужно проверить по датированным источникам и в разговоре.

## Threat model

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Кража Jira PAT | поле password; только RAM или read-only secret-file; Bearer только header; no access logs; allowlisted JSON logs | malware/root на ноутбуке видит process memory |
| Prompt injection в комментарии | Jira-текст только в user JSON между XML-границами; system priority; нет tools у модели; source allowlist; constrained output | модель может сформулировать нежелательный текст, поэтому policy validator + human review |
| JQL/path injection | строгий `^[A-Z][A-Z0-9]{1,19}-[1-9][0-9]{0,9}$`; search text фильтруется локально; query parameters | нестандартные Jira project keys потребуют осознанного изменения regex |
| MITM Jira | TLS verify всегда включён; corporate CA bundle; no redirect | компрометация корпоративного CA вне контроля приложения |
| Случайная публикация UI | host binding `127.0.0.1`; Trusted Host, CSP, CSRF, SameSite cookie; model port не опубликован | другой локальный процесс может обратиться к UI; ОС/endpoint protection остаются частью boundary |
| SSRF через base URL | URL валидируется, credentials/query запрещены, HTTPS обязателен кроме mock; Jira destination вводит пользователь | DNS rebinding/корпоративный DNS контролируются инфраструктурой |
| PII в логах | только event/code/correlation/duration; отключены httpx/access logs; stack trace не показывается UI | unexpected third-party stderr нужно проверять при обновлениях |
| Persisted PII | нет БД/caches/prompts; export только по действию; field mapping без PII | browser/download history и выбранный каталог экспорта контролирует пользователь |
| Supply-chain compromise | точные Python versions; base/runtime image digests; model URL commit + SHA-256; SBOM/CVE scripts | digest нужно обновлять вместе с security fixes после проверки |
| Resource exhaustion | response/comment/context limits; max pages; one concurrent generation; timeout/cancel | длинная CPU generation всё ещё нагружает ноутбук |

## Секреты

- Никогда не добавляйте PAT в `.env`, Compose, URL, issue key, комментарий, prompt или screenshot.
- Безопасный default: ввод в UI, затем «Завершить сессию». PAT исчезает при удалении сессии/перезапуске.
- Docker secret-file: `secrets/jira_token`, одна строка, права владельца. Каталог исключён из Git. На общем компьютере предпочтителен ввод в UI.
- HMAC key `secrets/hmac_key` генерируется случайно при подготовке модели. Его ротация изменит псевдоним `employee_ref`; данные не потеряются, потому что история не хранится.
- Desktop keychain не реализован: MVP поставляется как Docker web app. При создании native desktop wrapper использовать Windows Credential Manager/macOS Keychain/libsecret.

## Сетевой контур

В runtime приложению нужны только Jira и внутренний model service. Оно не содержит telemetry/CDN. Docker bridge сам по себе не является egress firewall: в строгом контуре примените корпоративный host firewall/proxy allowlist для Jira host и запретите иной egress контейнеру `app`; контейнеру `model` внешний egress не нужен вообще.

## Retention и безопасное удаление

Обычная остановка: `scripts/stop.sh` / `scripts/stop.ps1`. Для удаления session RAM нажмите «Завершить сессию» или остановите контейнер.

Экспорт удалите средствами ОС с учётом корпоративной политики. Field mapping не содержит PII. Полное локальное удаление после проверки текущего каталога:

```bash
docker compose down --volumes
```

Затем вручную удалите только `models/Qwen3-4B-Q4_K_M.gguf`, `secrets/hmac_key`, `secrets/jira_token` и собственные exports. Эти операции необратимы; приложение намеренно не автоматизирует их.

## Security checks

`./scripts/security-report.sh` формирует inventory и, если локально установлены `pip-audit`, `syft`, `grype`, CVE/SBOM reports. Отсутствующий scanner отмечается `NOT_RUN`, а не выдаётся за успешную проверку.

