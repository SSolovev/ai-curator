# Jira field mapping

Сопоставление полей зафиксировано в `FieldMapping` и автоматически применяется в каждой новой сессии. Отдельного экрана настройки и API изменения mapping нет. Файл `/data/field-mapping.json`, если он остался от предыдущей версии, больше не читается.

Известные MM identifiers встроены строго по ТЗ:

| Semantic | Jira ID |
|---|---|
| work-life balance | `customfield_12326` |
| work content | `customfield_12330` |
| client processes | `customfield_12332` |
| equipment | `customfield_12333` |
| recommendation | `customfield_12334` |
| manager interaction | `customfield_12335` |
| help contact | `customfield_16104` |
| IT1 Helps | `customfield_16105` |
| start date | `customfield_11418` |
| feedback comments | `customfield_14804` |

Для текущего Jira-каталога подтверждён следующий mapping:

| Semantic | Jira ID |
|---|---|
| role | `customfield_11411` |
| seniority | `customfield_11412` |
| department | `customfield_14101` |
| attestation date | `customfield_11459` |
| risk | `customfield_11435` |
| risk type | `customfield_11901` |
| value | `customfield_11434` |
| value type | `customfield_11900` |

Employee-задача запрашивается только с базовыми `summary/status/issuetype` и перечисленными выше mapped identifiers. Для дочерних MM/Feedback-задач используется отдельный явный allowlist системных, MM и feedback-полей; `fields=*all` не используется. Изменение Jira ID теперь требует изменения `FieldMapping` в коде и нового выпуска приложения.

Mock Jira использует тот же `FieldMapping` и при старте проверяет каталог полей и полную демо-карточку `DEMO-666`. Для Марии Тестовой (`DEMO-667`) есть отдельная карточка и комментарии для проверки обезличивания. Отсутствующие пользовательские поля в этой карточке остаются пустыми.

Issue type names MM/feedback задаются несекретными параметрами `CURATOR_MOOD_ISSUE_TYPE_NAMES` и `CURATOR_FEEDBACK_ISSUE_TYPE_NAMES`; child issues загружаются read-only с ограниченным набором полей и классифицируются локально. Это позволяет не вставлять изменяемые имена в JQL.

Статус завершённого MM определяется в `normalization.py`. Учитываются `Closed`, `Done`, `Resolved`, `Закрыт`, `Выполнено` и `Готово`. Для других статусов измените этот список в коде.
