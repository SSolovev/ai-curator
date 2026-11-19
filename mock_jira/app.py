from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from ai_curator.domain import FieldMapping

DATA = json.loads(
    (Path(__file__).parents[1] / "tests/fixtures/golden_jira.json").read_text(encoding="utf-8")
)
app = FastAPI(title="Mock Jira", docs_url=None, redoc_url=None)
EMPLOYEES = {employee["issue"]["key"]: employee for employee in DATA["employees"]}
PUBLISHED_COMMENTS: dict[str, list[dict]] = {}
COMMENT_IDS = count(8000)
DEFAULT_MAPPING = FieldMapping()
MAPPED_FIELD_IDS = frozenset(
    field_id for field_id in DEFAULT_MAPPING.model_dump().values() if field_id
)


def validate_demo_mapping() -> None:
    catalog_ids = {item.get("id") for item in DATA["fields"]}
    missing_catalog = MAPPED_FIELD_IDS - catalog_ids
    has_complete_employee = any(
        MAPPED_FIELD_IDS.issubset(employee["issue"]["fields"]) for employee in EMPLOYEES.values()
    )
    if missing_catalog or not has_complete_employee:
        raise RuntimeError(
            "Demo Jira fixture does not match FieldMapping: "
            f"missing catalog fields={sorted(missing_catalog)}, complete employee={has_complete_employee}"
        )


validate_demo_mapping()


class CommentBody(BaseModel):
    body: str


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
        name: deepcopy(value) for name, value in source_fields.items() if name in requested
    }
    return filtered


def employee_search_item(issue: dict) -> dict:
    return {
        "id": issue["id"],
        "key": issue["key"],
        "fields": {name: issue["fields"][name] for name in ("summary", "status", "issuetype")},
    }


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
    jql: str = Query(),
    fields: str = Query(default=""),
    startAt: int = Query(default=0),
    maxResults: int = Query(default=50),
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    if jql.startswith("parent = "):
        parent = jql.removeprefix("parent = ").partition(" ")[0]
        employee = EMPLOYEES.get(parent)
        issues = employee["children"] if employee is not None else []
    else:
        issues = [employee_search_item(employee["issue"]) for employee in EMPLOYEES.values()]
    if "status = Billable" in jql:
        issues = [
            x for x in issues if x.get("fields", {}).get("status", {}).get("name") == "Billable"
        ]
    page = [
        requested_issue_fields(issue, fields) for issue in issues[startAt : startAt + maxResults]
    ]
    return {"startAt": startAt, "maxResults": maxResults, "total": len(issues), "issues": page}


@app.get("/rest/api/2/issue/{key}")
def issue(
    key: str,
    fields: str = Query(default=""),
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    employee = EMPLOYEES.get(key)
    if employee is None:
        raise HTTPException(status_code=404, detail="Not found")
    return requested_issue_fields(employee["issue"], fields)


@app.get("/rest/api/2/issue/{key}/comment")
def comments(
    key: str,
    startAt: int = Query(default=0),
    maxResults: int = Query(default=50),
    orderBy: str = Query(default="created"),
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    employee = EMPLOYEES.get(key)
    if employee is None:
        raise HTTPException(status_code=404, detail="Not found")
    items = sorted(
        [*employee["comments"], *PUBLISHED_COMMENTS.get(key, [])],
        key=lambda item: item.get("created", ""),
        reverse=orderBy == "-created",
    )
    return {
        "startAt": startAt,
        "maxResults": maxResults,
        "total": len(items),
        "comments": items[startAt : startAt + maxResults],
    }


@app.post("/rest/api/2/issue/{key}/comment", status_code=201)
async def add_comment(
    key: str,
    comment: CommentBody,
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    if key not in EMPLOYEES:
        raise HTTPException(status_code=404, detail="Not found")
    if not comment.body.strip():
        raise HTTPException(status_code=400, detail="Empty comment")
    created = datetime.now(UTC).isoformat()
    published = {"id": str(next(COMMENT_IDS)), "created": created, "body": comment.body}
    PUBLISHED_COMMENTS.setdefault(key, []).append(published)
    return published
