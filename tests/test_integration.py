from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai_curator.config import Settings
from ai_curator.domain import FieldMapping
from ai_curator.export import markdown_export
from ai_curator.jira import JiraClient, JiraTransportConfig
from ai_curator.main import create_app
from ai_curator.normalization import MM_FIELDS, normalize_bundle
from ai_curator.privacy_filter import PersonalDataFilter, load_dictionary
from ai_curator.service import CuratorService
from ai_curator.session import SessionState
from mock_jira.app import PUBLISHED_COMMENTS
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


def test_golden_full_path_mock_jira_normalization_mock_llm_rendered_result(
    normalized, valid_result, noop_privacy_filter
):
    llm = GoldenMockLLM(valid_result)
    service = CuratorService(
        Settings(),
        ROOT,
        llm=llm,
        privacy_filter=noop_privacy_filter,
    )
    state = SessionState(context=normalized)
    output = asyncio.run(service.generate(state, "golden-generation", {"jira-comment:7004"}))
    rendered = markdown_export(output)
    assert "Алексей Андреевич Демов" not in llm.received[1]["content"]
    assert "jira-comment:7004" not in llm.received[1]["content"]
    assert output["agenda"][-1]["end_minute"] == 30
    assert all(item["evidence"] for item in output["meeting_priorities"])
    assert "уволить" not in rendered.casefold()
    assert "Сценарий встречи" in rendered


def test_actual_mock_jira_api_to_normalization_and_model(
    mapping, valid_result, noop_privacy_filter
):
    async def scenario():
        transport = httpx.ASGITransport(app=mock_jira_app)
        client = JiraClient(
            JiraTransportConfig(
                base_url="http://localhost:9000",
                token="demo-token",  # noqa: S106 - mock only
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
        service = CuratorService(
            Settings(),
            ROOT,
            llm=llm,
            privacy_filter=noop_privacy_filter,
        )
        output = await service.generate(SessionState(context=context), "integration-e2e", set())
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
    assert 11 <= len(context.interactions) <= 17
    assert len(context.interactions) == 15
    assert any(item.type == "MM" for item in context.interactions)
    assert "Алексей Андреевич Демов" not in llm.received[1]["content"]
    assert output["agenda"][-1]["end_minute"] == 30


def test_mock_jira_returns_only_requested_employee_fields(mapping):
    async def scenario():
        transport = httpx.ASGITransport(app=mock_jira_app)
        client = JiraClient(
            JiraTransportConfig(
                base_url="http://localhost:9000",
                token="demo-token",  # noqa: S106
            ),
            transport=transport,
        )
        try:
            return await client.employee_issue("DEMO-666", ["summary", mapping.role, mapping.risk])
        finally:
            await client.close()

    employee = asyncio.run(scenario())
    assert set(employee["fields"]) == {"summary", mapping.role, mapping.risk}
    assert employee["fields"][mapping.role] == {"value": "Java"}


def test_maria_demo_history_exercises_depersonalization():
    detected_terms = {
        "Мария Сергеевна Тестова": "PERSON",
        "Мария": "PERSON",
        "Павел Игоревич Макетов": "PERSON",
        "г. Примерск, ул. Макетная, д. 42, кв. 17": "ADDRESS",
    }

    class DemoDetector:
        async def generate(self, messages, schema, cancel_event):
            content = messages[1]["content"]
            records_json = content.split("<records>\n", 1)[1].split("\n</records>", 1)[0]
            return {
                "matches": [
                    {"record_id": record["record_id"], "text": term, "category": category}
                    for record in json.loads(records_json)
                    for term, category in detected_terms.items()
                    if term in record["text"]
                ]
            }

    async def scenario():
        config = JiraTransportConfig(
            base_url="http://mock-jira:9000",
            token="demo-token",  # noqa: S106
        )
        async with JiraClient(config, transport=httpx.ASGITransport(app=mock_jira_app)) as jira:
            employee = await jira.employee_issue("DEMO-667", ["summary", "issuetype", "status"])
            comments = await jira.comments("DEMO-667")
            children = await jira.child_issues("DEMO-667", ["summary", "issuetype", "status"])
        assert children == []
        context = normalize_bundle(
            employee_issue=employee,
            comments=comments,
            children=children,
            mapping=FieldMapping(),
            hmac_key=b"demo-test-hmac-key",
            current_time=datetime(2026, 8, 20, tzinfo=UTC),
            include_display_name=True,
        )
        detector = PersonalDataFilter(
            DemoDetector(),
            load_dictionary(ROOT / "docs/pii_dictionary.example.txt"),
            ROOT / "prompts/pii_filter.system.txt",
        )
        filtered = await detector.sanitize_context(context, asyncio.Event())
        return context.model_dump_json(), filtered.context.model_dump_json()

    original, sanitized = asyncio.run(scenario())
    for term in (
        *detected_terms,
        "maria.testova@example.invalid",
        "pavel.maketov@example.invalid",
        "+7 (000) 000-67-70",
        "@mtestova_demo",
        "Демо-Контур 667",
        "DEMO-HR-00667",
    ):
        assert term in original
        assert term not in sanitized
    assert "Порядок ревью согласован" in sanitized
    assert "коллеги [PII_EMAIL]. Следующий шаг" in sanitized
    for category in ("PERSON", "ADDRESS", "EMAIL", "PHONE", "ACCOUNT", "DICTIONARY"):
        assert f"[PII_{category}]" in sanitized


def test_publish_edited_scenario_to_original_demo_employee(monkeypatch, noop_privacy_filter):
    monkeypatch.setitem(PUBLISHED_COMMENTS, "DEMO-667", [])
    monkeypatch.setattr(
        "ai_curator.service.JiraClient",
        lambda config: JiraClient(config, transport=httpx.ASGITransport(app=mock_jira_app)),
    )
    llm = GoldenMockLLM(
        {
            "history_summary": {
                "recurring_topics": [
                    {
                        "statement": "Порядок ревью согласован.",
                        "certainty": "fact",
                        "evidence": [{"source_ref": "jira-comment:7702", "date": "2026-08-11"}],
                    }
                ],
            },
        }
    )
    settings = Settings()
    service = CuratorService(settings, ROOT, llm=llm, privacy_filter=noop_privacy_filter)
    connection = {"jira_base_url": "http://mock-jira:9000", "token": "demo-token"}

    with TestClient(create_app(settings, service)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
        assert client.post("/api/connect", headers=headers, json=connection).status_code == 200
        matches = client.get("/api/employees", params={"q": "Мария Сергеевна Тестова"})
        assert [item["key"] for item in matches.json()["items"]] == ["DEMO-667"]
        loaded = client.post("/api/employees/DEMO-667/load", headers=headers)
        assert loaded.status_code == 200
        assert loaded.json()["employee"]["jira_key"] == "DEMO-667"
        assert len(loaded.json()["interactions"]) == 3
        assert loaded.json()["mood_monitoring"] == []
        assert loaded.json()["context_selection"]["warnings"] == []

        started = client.post("/api/generate", headers=headers, json={})
        assert started.status_code == 202
        generation_id = started.json()["generation_id"]
        path = f"/api/generate/{generation_id}"
        for _ in range(50):
            completed = client.get(f"{path}/result")
            if completed.status_code != 202:
                break
            time.sleep(0.01)
        assert completed.status_code == 200, completed.text
        assert completed.json()["jira_issue_key"] == "DEMO-667"
        assert completed.json()["jira_base_url"] == "http://mock-jira:9000"

        # Loading a different person doesn't change the destination of an earlier scenario.
        assert client.post("/api/employees/DEMO-666/load", headers=headers).status_code == 200
        draft = {"text": "Правки куратора.\n\nОбсудить порядок ревью."}
        assert client.post(f"{path}/comment", json=draft).status_code == 403
        posted = client.post(f"{path}/comment", headers=headers, json=draft)
        assert posted.status_code == 200
        assert posted.json()["issue_key"] == "DEMO-667"
        repeated = client.post(f"{path}/comment", headers=headers, json=draft)
        assert repeated.json() == posted.json()
        assert len(PUBLISHED_COMMENTS["DEMO-667"]) == 1
        assert PUBLISHED_COMMENTS["DEMO-667"][0]["body"] == f"!СГЕНЕРИРОВАНО ИИ!\n\n{draft['text']}"

        assert client.post("/api/connect", headers=headers, json=connection).status_code == 200
        assert client.get(f"{path}/result").json()["jira_issue_key"] is None
        assert client.get(f"{path}/result").json()["jira_base_url"] is None
        blocked = client.post(f"{path}/comment", headers=headers, json={"text": "Другой текст."})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "JIRA_CONNECTION_CHANGED"
