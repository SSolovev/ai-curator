from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ai_curator.config import Settings
from ai_curator.jira import JiraClient, JiraTransportConfig
from ai_curator.normalization import MM_FIELDS, normalize_bundle
from ai_curator.service import CuratorService, SessionState, markdown_export
from mock_jira.app import app as mock_jira_app

ROOT = Path(__file__).parents[1]


class GoldenMockLLM:
    def __init__(self, response):
        self.response = response
        self.received = None

    async def health(self):
        return {"ok": True}

    async def generate(self, messages, schema, cancel_event):
        self.received = messages
        return copy.deepcopy(self.response)


def test_golden_full_path_mock_jira_normalization_mock_llm_rendered_result(normalized, valid_result, tmp_path):
    llm = GoldenMockLLM(valid_result)
    service = CuratorService(Settings(data_dir=tmp_path), ROOT, llm=llm)
    state = SessionState(context=normalized)
    output = asyncio.run(service.generate(state, "golden-generation", {"jira-comment:7004"}, False))
    rendered = markdown_export(output)
    assert "Алексей Андреевич Демов" not in llm.received[1]["content"]
    assert "jira-comment:7004" not in llm.received[1]["content"]
    assert output["agenda"][-1]["end_minute"] == 30
    assert all(item["evidence"] for item in output["meeting_priorities"])
    assert "уволить" not in rendered.casefold()
    assert "Сценарий встречи" in rendered


def test_actual_mock_jira_api_to_normalization_and_model(mapping, valid_result, tmp_path):
    async def scenario():
        transport = httpx.ASGITransport(app=mock_jira_app)
        client = JiraClient(
            JiraTransportConfig(
                base_url="http://localhost:9000", token="demo-token"  # noqa: S106 - mock only
            ),
            transport=transport,
        )
        try:
            assert (await client.myself())["active"] is True
            field_catalog = await client.fields()
            catalog_ids = {item["id"] for item in field_catalog}
            assert set(mapping.model_dump().values()).issubset(catalog_ids)
            employees = await client.employee_list(page_size=1)
            employee = await client.employee_issue(
                "DEMO-666",
                [
                    "summary",
                    "status",
                    "issuetype",
                    *[value for value in mapping.model_dump().values() if value],
                ],
            )
            assert set(mapping.model_dump().values()).issubset(employee["fields"])
            comments = await client.comments("DEMO-666")
            children = await client.child_issues(
                "DEMO-666",
                fields=[
                    "summary",
                    "status",
                    "created",
                    "issuetype",
                    "description",
                    "customfield_11418",
                    "customfield_14804",
                    *MM_FIELDS.values(),
                ],
                page_size=2,
            )
        finally:
            await client.close()
        context = normalize_bundle(
            employee_issue=employee,
            comments=comments,
            children=children,
            mapping=mapping,
            hmac_key=b"integration-test-hmac-key",
            current_time=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
            include_display_name=True,
        )
        llm = GoldenMockLLM(valid_result)
        service = CuratorService(Settings(data_dir=tmp_path), ROOT, llm=llm)
        output = await service.generate(SessionState(context=context), "integration-e2e", set(), False)
        return employees, context, output, llm

    employees, context, output, llm = asyncio.run(scenario())
    assert len(employees) == 2
    assert employees[0]["key"] == "DEMO-666"
    assert set(context.employee.model_dump()) >= {
        "role",
        "seniority",
        "department",
        "attestation_date",
        "jira_recorded_risk",
        "jira_recorded_value",
    }
    assert context.employee.role == "Java"
    assert context.employee.seniority == "Senior"
    assert context.employee.department == "Подразделение разработки платформы"
    assert context.employee.jira_recorded_risk.level == "4"
    assert context.employee.jira_recorded_risk.type == "Содержание работы"
    assert context.employee.jira_recorded_value.level == "5"
    assert context.employee.jira_recorded_value.types == [
        "Экспертиза",
        "Performance",
        "Интервьюирование",
    ]
    assert len(context.mood_monitoring) == 3
    assert len(context.interactions) == 50
    assert "Алексей Андреевич Демов" not in llm.received[1]["content"]
    assert output["agenda"][-1]["end_minute"] == 30


def test_mock_jira_returns_only_requested_employee_fields(mapping):
    async def scenario():
        transport = httpx.ASGITransport(app=mock_jira_app)
        client = JiraClient(
            JiraTransportConfig(
                base_url="http://localhost:9000", token="demo-token"  # noqa: S106
            ),
            transport=transport,
        )
        try:
            return await client.employee_issue(
                "DEMO-666", ["summary", mapping.role, mapping.risk]
            )
        finally:
            await client.close()

    employee = asyncio.run(scenario())
    assert set(employee["fields"]) == {"summary", mapping.role, mapping.risk}
    assert employee["fields"][mapping.role] == {"value": "Java"}
