from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

DATA = json.loads((Path(__file__).parents[1] / "tests/fixtures/golden_jira.json").read_text(encoding="utf-8"))
app = FastAPI(title="Mock Jira", docs_url=None, redoc_url=None)


def authorized(authorization: str | None) -> None:
    if authorization != "Bearer demo-token":
        raise HTTPException(status_code=401, detail="Unauthorized")


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
    page = issues[startAt : startAt + maxResults]
    return {"startAt": startAt, "maxResults": maxResults, "total": len(issues), "issues": page}


@app.get("/rest/api/2/issue/{key}")
def issue(key: str, authorization: str | None = Header(default=None)):
    authorized(authorization)
    if key != DATA["employee"]["key"]:
        raise HTTPException(status_code=404, detail="Not found")
    return DATA["employee"]


@app.get("/rest/api/2/issue/{key}/comment")
def comments(
    key: str, startAt: int = Query(default=0), maxResults: int = Query(default=50),
    authorization: str | None = Header(default=None),
):
    authorized(authorization)
    if key != DATA["employee"]["key"]:
        raise HTTPException(status_code=404, detail="Not found")
    items = DATA["comments"]
    return {"startAt": startAt, "maxResults": maxResults, "total": len(items), "comments": items[startAt : startAt + maxResults]}
