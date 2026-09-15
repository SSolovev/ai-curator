from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

from ai_curator.domain import FieldMapping

DATA = json.loads((Path(__file__).parents[1] / "tests/fixtures/golden_jira.json").read_text(encoding="utf-8"))
app = FastAPI(title="Mock Jira", docs_url=None, redoc_url=None)
DEFAULT_MAPPING = FieldMapping()
MAPPED_FIELD_IDS = frozenset(
    field_id for field_id in DEFAULT_MAPPING.model_dump().values() if field_id
)


def validate_demo_mapping() -> None:
    catalog_ids = {item.get("id") for item in DATA["fields"]}
    employee_field_ids = set(DATA["employee"].get("fields", {}))
    missing_catalog = MAPPED_FIELD_IDS - catalog_ids
    missing_employee = MAPPED_FIELD_IDS - employee_field_ids
    if missing_catalog or missing_employee:
        raise RuntimeError(
            "Demo Jira fixture does not match FieldMapping: "
            f"catalog={sorted(missing_catalog)}, employee={sorted(missing_employee)}"
        )


validate_demo_mapping()


ARCHIVE_COMMENT_TEXTS = (
    "Обсудили текущие задачи и приоритеты. Критичных блокеров нет.",
    "Содержание проекта остаётся интересным; хочется чаще участвовать в технических обсуждениях.",
    "Договорились уточнить ожидания по следующему этапу работ и вернуться с ответом.",
    "Рабочая нагрузка высокая, но управляемая. Стоит проверить ситуацию на следующей встрече.",
    "Получена положительная обратная связь от команды за качество ревью.",
    "Есть задержки в согласованиях со смежной командой; договорились собрать конкретные примеры.",
    "Обсудили развитие экспертизы и возможную тему внутреннего доклада.",
    "Предыдущая договорённость выполнена, доступ к рабочему стенду получен.",
)


def authorized(authorization: str | None) -> None:
    if authorization != "Bearer demo-token":
        raise HTTPException(status_code=401, detail="Unauthorized")


def requested_issue_fields(issue: dict, fields: str) -> dict:
    requested = {value.strip() for value in fields.split(",") if value.strip()}
    if not requested or "*all" in requested:
        return deepcopy(issue)
    filtered = {key: deepcopy(value) for key, value in issue.items() if key != "fields"}
    source_fields = issue.get("fields", {})
    filtered["fields"] = {
        name: deepcopy(value)
        for name, value in source_fields.items()
        if name in requested
    }
    return filtered


def demo_comments() -> list[dict]:
    """Return 50 deterministic, synthetic comments with Jira-like payloads."""
    comments = deepcopy(DATA["comments"])
    archive_count = max(0, 50 - len(comments))
    for index in range(archive_count):
        year = 2021 + index // 12
        month = index % 12 + 1
        meeting_type = "👥 1-1" if index % 3 else "😊 MM"
        comments.append(
            {
                "id": str(6900 + index),
                "created": datetime(year, month, 15, 12, 0).isoformat()
                + "+0300",
                "updated": datetime(year, month, 15, 12, 0).isoformat()
                + "+0300",
                "body": (
                    f"{{meeting-type}}{meeting_type}{{meeting-type}} "
                    f"{ARCHIVE_COMMENT_TEXTS[index % len(ARCHIVE_COMMENT_TEXTS)]}"
                ),
                "author": {
                    "self": "http://mock-jira:9000/rest/api/2/user?username=demo-curator",
                    "name": "demo-curator",
                    "displayName": "Демо-куратор",
                    "active": True,
                },
                "visibility": {"type": "role", "value": "Curators"},
            }
        )
    return comments


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/rest/api/2/myself")
def myself(authorization: str | None = Header(default=None)):
    authorized(authorization)
    return {"active": True, "accountId": "mock-curator"}


@app.get("/rest/api/2/field")
def fields(authorization: str | None = Header(default=None)):
    authorized(authorization)
    return DATA["fields"]


@app.get("/rest/api/2/search")
def search(
    jql: str = Query(), fields: str = Query(default=""), startAt: int = Query(default=0),
    maxResults: int = Query(default=50), authorization: str | None = Header(default=None),
):
    authorized(authorization)
    issues = DATA["children"] if "parent =" in jql else DATA["employees"]
    if "status = Billable" in jql:
        issues = [x for x in issues if x.get("fields", {}).get("status", {}).get("name") == "Billable"]
    page = [
        requested_issue_fields(issue, fields)
        for issue in issues[startAt : startAt + maxResults]
    ]
    return {"startAt": startAt, "maxResults": maxResults, "total": len(issues), "issues": page}


@app.get("/rest/api/2/issue/{key}")
def issue(
    key: str,
    fields: str = Query(default=""),
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    if key != DATA["employee"]["key"]:
        raise HTTPException(status_code=404, detail="Not found")
    return requested_issue_fields(DATA["employee"], fields)


@app.get("/rest/api/2/issue/{key}/comment")
def comments(
    key: str, startAt: int = Query(default=0), maxResults: int = Query(default=50),
    orderBy: str = Query(default="created"),
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    if key != DATA["employee"]["key"]:
        raise HTTPException(status_code=404, detail="Not found")
    items = sorted(
        demo_comments(),
        key=lambda item: item.get("created", ""),
        reverse=orderBy == "-created",
    )
    return {"startAt": startAt, "maxResults": maxResults, "total": len(items), "comments": items[startAt : startAt + maxResults]}
