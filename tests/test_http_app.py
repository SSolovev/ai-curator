from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from ai_curator.config import Settings
from ai_curator.main import create_app


class HTTPFakeService:
    def __init__(self, result):
        self.result = result

    async def health(self):
        return {"ok": True}

    async def connect(self, state, **kwargs):
        state.jira_config = object()
        return {"ok": True, "authenticated": True}

    async def employees(self, state, query, billable_only):
        return [
            {
                "key": "DEMO-1001",
                "summary": "Вымышленный сотрудник",
                "status": "Billable",
            }
        ]

    async def field_catalog(self, state):
        return {"fields": [], "mapping": state.mapping.model_dump(), "warnings": []}

    def mapping_warnings(self, mapping):
        return []

    async def load_employee(self, state, key, include_display_name):
        return {
            "employee": {"jira_key": key, "display_name": None},
            "context_selection": {"warnings": []},
        }

    async def generate(self, state, generation_id, excluded, include_display_name):
        return copy.deepcopy(self.result)

    def cancel(self, state, generation_id):
        return True

    @property
    def llm(self):
        return self


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://model.vendor.example/v1",
        "http://user:password@localhost:8080",
        "http://localhost:8080?forward=external",
    ],
)
def test_settings_reject_external_or_credentialed_llm_endpoints(url):
    with pytest.raises(ValueError):
        Settings(llm_base_url=url)


def test_settings_accept_local_llm_endpoints():
    assert Settings(llm_base_url="http://model:8080/").llm_base_url == "http://model:8080"
    assert Settings(llm_base_url="http://host.docker.internal:8080").llm_base_url.endswith(":8080")


def test_http_security_headers_csrf_and_session(valid_result, tmp_path):
    app = create_app(
        Settings(production=False, data_dir=tmp_path), HTTPFakeService(valid_result)
    )
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert index.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in index.headers["Content-Security-Policy"]
        rejected = client.post(
            "/api/connect",
            json={"jira_base_url": "https://jira.local", "token": "secret"},
        )
        assert rejected.status_code == 403
        csrf = client.get("/api/session").json()["csrf_token"]
        connected = client.post(
            "/api/connect",
            headers={"X-CSRF-Token": csrf},
            json={"jira_base_url": "https://jira.local", "token": "secret"},
        )
        assert connected.status_code == 200
        assert "secret" not in connected.text
        employees = client.get("/api/employees?page=1&page_size=20")
        assert employees.status_code == 200
        assert employees.json()["total"] == 1
        deleted = client.delete("/api/session", headers={"X-CSRF-Token": csrf})
        assert deleted.status_code == 200
