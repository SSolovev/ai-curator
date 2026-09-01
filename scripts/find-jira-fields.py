from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def parse_event(line: str) -> dict[str, Any] | None:
    start = line.find("{")
    if start < 0:
        return None
    try:
        event = json.loads(line[start:])
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def latest_catalog(lines: list[str]) -> list[dict[str, Any]] | None:
    catalog: list[dict[str, Any]] | None = None
    for line in lines:
        event = parse_event(line)
        if not event or event.get("event") != "jira_field_catalog_response":
            continue
        response_body = event.get("response_body")
        if not isinstance(response_body, str):
            continue
        try:
            value = json.loads(response_body)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            catalog = [item for item in value if isinstance(item, dict)]
    return catalog


def latest_issue(lines: list[str]) -> dict[str, Any] | None:
    issue: dict[str, Any] | None = None
    for line in lines:
        event = parse_event(line)
        if not event or event.get("event") != "jira_employee_issue_response":
            continue
        response_body = event.get("response_body")
        if not isinstance(response_body, str):
            continue
        try:
            value = json.loads(response_body)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            issue = value
    return issue


def one_line(value: Any) -> str:
    return " ".join(str(value or "").replace("\t", " ").split())


def rendered_value(fields: dict[str, Any], field_id: str) -> str:
    if field_id not in fields:
        return "<нет в ответе>"
    return one_line(json.dumps(fields[field_id], ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Найти поля в последнем jira_field_catalog_response из stdin."
    )
    parser.add_argument("--raw", action="store_true", help="Вывести полный каталог как форматированный JSON")
    parser.add_argument("--raw-issue", action="store_true", help="Вывести полную выбранную задачу как JSON")
    parser.add_argument("query", nargs="*", help="Слова для поиска по описанию или значению поля")
    args = parser.parse_args()

    lines = sys.stdin.readlines()
    catalog = latest_catalog(lines)
    issue = latest_issue(lines)
    if catalog is None:
        print(
            "Событие jira_field_catalog_response не найдено. "
            "Откройте этап проверки полей Jira и повторите команду.",
            file=sys.stderr,
        )
        return 2

    if args.raw:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
        return 0
    if args.raw_issue:
        if issue is None:
            print(
                "Событие jira_employee_issue_response не найдено. "
                "Сохраните mapping и дождитесь загрузки выбранного сотрудника.",
                file=sys.stderr,
            )
            return 2
        print(json.dumps(issue, ensure_ascii=False, indent=2))
        return 0

    issue_fields = issue.get("fields") if issue and isinstance(issue.get("fields"), dict) else {}

    terms = [term.casefold() for term in args.query if term.strip()]
    matches: list[dict[str, Any]] = []
    for field in catalog:
        field_id = str(field.get("id", ""))
        searchable = json.dumps(
            {"definition": field, "value": issue_fields.get(field_id)},
            ensure_ascii=False,
            sort_keys=True,
        ).casefold()
        if all(term in searchable for term in terms):
            matches.append(field)

    print("id\tname\ttype_or_custom\tclause_names\tvalue")
    for field in sorted(matches, key=lambda item: (one_line(item.get("name")).casefold(), one_line(item.get("id")))):
        schema = field.get("schema") if isinstance(field.get("schema"), dict) else {}
        field_type = schema.get("custom") or schema.get("system") or schema.get("type") or ""
        clause_names = field.get("clauseNames") if isinstance(field.get("clauseNames"), list) else []
        field_id = one_line(field.get("id"))
        print(
            "\t".join(
                (
                    field_id,
                    one_line(field.get("name")),
                    one_line(field_type),
                    one_line(", ".join(str(item) for item in clause_names)),
                    rendered_value(issue_fields, field_id),
                )
            )
        )
    if issue is None:
        print(
            "Значения не найдены: после открытия каталога сохраните mapping и дождитесь загрузки сотрудника.",
            file=sys.stderr,
        )
    print(f"\nНайдено: {len(matches)} из {len(catalog)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
