# Jira field mapping

Приложение читает `/rest/api/2/field`, показывает `name (id)` и сохраняет только выбранные identifiers в Docker volume `/data/field-mapping.json`. ФИО, Jira key и значения полей туда не входят.

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
| feedback comments | `customfield_14804` (default, можно изменить) |

`Role`, `Seniority`, `Department`, `Attestation Date`, `Risk`, `Risk Type`, `Value`, `Value Type` нужно проверить вручную. При отсутствии каталога metadata экран оставляет mapping пустым/сохранённым и показывает предупреждение. Не выбирайте поле только по похожему названию: уточните ID у Jira administrator. Пустой mapping означает `null`, не fallback на «похожее» поле.

Issue type names MM/feedback задаются несекретными параметрами `CURATOR_MOOD_ISSUE_TYPE_NAMES` и `CURATOR_FEEDBACK_ISSUE_TYPE_NAMES`; все child issues загружаются read-only и классифицируются локально. Это позволяет не вставлять изменяемые имена в JQL.

Статус завершённого MM сейчас распознаёт common English/Russian names (`Closed`, `Done`, `Resolved`, `Закрыт`, `Выполнено`, `Готово`). Если workflow использует другие значения, расширьте конфигурацию до подключения production Jira.
