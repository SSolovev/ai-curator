from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from ai_curator.errors import CuratorError
from ai_curator.jira import JiraClient, JiraTransportConfig, validate_employee_key


def run(coro):
    return asyncio.run(coro)


def config(**overrides):
    values = {"base_url": "http://localhost:9000", "token": "top-secret", "max_retries": 0}
    values.update(overrides)
    return JiraTransportConfig(**values)


def test_search_pagination_and_query_parameters_are_encoded():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        start = int(request.url.params["startAt"])
        issues = [{"key": f"DEMO-{start + 1}", "fields": {}}] if start < 2 else []
        return httpx.Response(200, json={"startAt": start, "maxResults": 1, "total": 2, "issues": issues})

    async def scenario():
        client = JiraClient(config(), transport=httpx.MockTransport(handler))
        try:
            return await client.search_all(jql='summary ~ "A & B"', fields=["summary", "status"], page_size=1)
        finally:
            await client.close()

    issues = run(scenario())
    assert [x["key"] for x in issues] == ["DEMO-1", "DEMO-2"]
    assert requests[0].url.params["jql"] == 'summary ~ "A & B"'
    assert requests[0].url.params["fields"] == "summary,status"
    assert "A+%26+B" in str(requests[0].url) or "A%20%26%20B" in str(requests[0].url)
    assert requests[0].headers["Authorization"] == "Bearer top-secret"
    assert "top-secret" not in str(requests[0].url)


@pytest.mark.parametrize("key", ["DEMO-1 OR 1=1", "demo';DROP-1", "A-0", "../DEMO-1", "DEMO-1 ORDER BY"])
def test_employee_key_blocks_jql_and_path_injection(key):
    with pytest.raises(CuratorError) as caught:
        validate_employee_key(key)
    assert caught.value.code == "EMPLOYEE_KEY_INVALID"


def test_employee_search_user_text_never_enters_jql():
    captured = None

    def handler(request: httpx.Request):
        nonlocal captured
        captured = request.url.params["jql"]
        return httpx.Response(200, json={"startAt": 0, "total": 1, "issues": [{"key": "DEMO-1", "fields": {"summary": "Alice OR status", "status": {"name": "Open"}}}]})

    async def scenario():
        client = JiraClient(config(), transport=httpx.MockTransport(handler))
        try:
            return await client.employee_list(query='" OR status != Closed')
        finally:
            await client.close()

    run(scenario())
    assert '" OR status != Closed' not in captured


@pytest.mark.parametrize("status,code", [(401, "JIRA_HTTP_401"), (403, "JIRA_HTTP_403"), (404, "JIRA_HTTP_404"), (429, "JIRA_HTTP_429"), (500, "JIRA_HTTP_500")])
def test_http_errors_have_safe_codes(status, code):
    async def scenario():
        client = JiraClient(config(), transport=httpx.MockTransport(lambda _: httpx.Response(status, text="secret body")))
        try:
            return await client.myself()
        finally:
            await client.close()

    with pytest.raises(CuratorError) as caught:
        run(scenario())
    assert caught.value.code == code
    assert "secret body" not in caught.value.user_message
    assert "top-secret" not in caught.value.user_message


@pytest.mark.parametrize("body,code", [(b"", "JIRA_EMPTY_RESPONSE"), (b"{broken", "JIRA_INVALID_JSON")])
def test_empty_and_malformed_json(body, code):
    async def scenario():
        client = JiraClient(config(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
        try:
            return await client.myself()
        finally:
            await client.close()

    with pytest.raises(CuratorError) as caught:
        run(scenario())
    assert caught.value.code == code


def test_invalid_json_can_log_redacted_response_preview(caplog):
    body = (
        '<html>Bearer top-secret<input name="csrf_token" value="csrf-secret">'
        + "x" * 500
        + "</html>"
    ).encode()

    async def scenario():
        client = JiraClient(
            config(log_invalid_response_body=True, log_response_max_chars=128),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    content=body,
                )
            ),
        )
        try:
            return await client.myself()
        finally:
            await client.close()

    with caplog.at_level(logging.WARNING, logger="ai_curator.jira"):
        with pytest.raises(CuratorError):
            run(scenario())

    record = next(record for record in caplog.records if record.msg == "jira_invalid_json_response")
    assert record.status_code == 200
    assert record.content_type == "text/html; charset=utf-8"
    assert record.request_path == "/rest/api/2/myself"
    assert record.response_truncated is True
    assert len(record.response_body) == 128
    assert "top-secret" not in record.response_body
    assert "csrf-secret" not in record.response_body
    assert "[REDACTED]" in record.response_body


def test_invalid_json_response_body_logging_is_disabled_by_default(caplog):
    async def scenario():
        client = JiraClient(
            config(),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"private html")),
        )
        try:
            return await client.myself()
        finally:
            await client.close()

    with caplog.at_level(logging.WARNING, logger="ai_curator.jira"):
        with pytest.raises(CuratorError):
            run(scenario())

    record = next(record for record in caplog.records if record.msg == "jira_invalid_json_response")
    assert record.response_body is None


def test_field_catalog_logging_contains_full_response(caplog):
    fields = [
        {
            "id": f"customfield_{index}",
            "name": f"Поле {index}",
            "schema": {"type": "string", "custom": "example:field"},
        }
        for index in range(100)
    ]

    async def scenario():
        client = JiraClient(
            config(log_field_catalog=True),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=fields)),
        )
        try:
            return await client.fields()
        finally:
            await client.close()

    with caplog.at_level(logging.INFO, logger="ai_curator.jira"):
        result = run(scenario())

    record = next(record for record in caplog.records if record.msg == "jira_field_catalog_response")
    assert result == fields
    assert json.loads(record.response_body) == fields
    assert record.request_path == "/rest/api/2/field"
    assert record.response_truncated is False


def test_employee_issue_logging_contains_full_response(caplog):
    issue = {
        "id": "5816",
        "key": "GPBHIRE-5816",
        "fields": {
            "summary": "Сотрудник",
            "customfield_12345": {"value": "High"},
        },
    }

    async def scenario():
        client = JiraClient(
            config(log_employee_issue=True),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=issue)),
        )
        try:
            return await client.employee_issue("GPBHIRE-5816", ["*all"])
        finally:
            await client.close()

    with caplog.at_level(logging.INFO, logger="ai_curator.jira"):
        result = run(scenario())

    record = next(record for record in caplog.records if record.msg == "jira_employee_issue_response")
    assert result == issue
    assert json.loads(record.response_body) == issue
    assert record.request_path == "/rest/api/2/issue/GPBHIRE-5816"
    assert record.response_truncated is False


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ReadTimeout("slow"), "JIRA_TIMEOUT"),
        (httpx.ConnectError("certificate verify failed"), "JIRA_TLS"),
    ],
)
def test_transport_errors(exception, code):
    def handler(_: httpx.Request):
        raise exception

    async def scenario():
        client = JiraClient(config(), transport=httpx.MockTransport(handler))
        try:
            return await client.myself()
        finally:
            await client.close()

    with pytest.raises(CuratorError) as caught:
        run(scenario())
    assert caught.value.code == code


def test_missing_ca_bundle_has_actionable_error(tmp_path):
    with pytest.raises(CuratorError) as caught:
        JiraClient(config(ca_bundle_path=str(tmp_path / "missing-ca.pem")))
    assert caught.value.code == "CA_NOT_FOUND"


def test_comment_pagination_limit_is_not_silent():
    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "startAt": 0,
                "maxResults": 1,
                "total": 2,
                "comments": [{"id": "1", "body": "text"}],
            },
        )

    async def scenario():
        client = JiraClient(config(), transport=httpx.MockTransport(handler))
        try:
            return await client.comments("DEMO-1", page_size=1, max_pages=1)
        finally:
            await client.close()

    with pytest.raises(CuratorError) as caught:
        run(scenario())
    assert caught.value.code == "JIRA_PAGINATION_LIMIT"
