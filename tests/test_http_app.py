from __future__ import annotations

import asyncio
import copy
import time

import pytest
from fastapi.testclient import TestClient

from ai_curator.config import Settings
from ai_curator.domain import FieldMapping
from ai_curator.main import create_app
from ai_curator.service import SessionStore


class HTTPFakeService:
    def __init__(self, result):
        self.result = result

    async def health(self):
        return {"ok": True}

    async def model_diagnostics(self):
        return {
            "runtime": "llama_cpp",
            "runtime_label": "llama.cpp (Docker)",
            "configured_model": "test-model",
            "health": {"ok": True},
            "contains_personal_data": False,
        }

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

    async def load_employee(self, state, key, include_display_name):
        state.context = {
            "employee": {"jira_key": key, "display_name": None},
            "context_selection": {"warnings": []},
        }
        return state.context

    async def generate(self, state, generation_id, excluded, include_display_name):
        return copy.deepcopy(self.result)

    def cancel(self, state, generation_id):
        return True

    @property
    def llm(self):
        return self


class HTTPBlockingFakeService(HTTPFakeService):
    async def generate(self, state, generation_id, excluded, include_display_name):
        await asyncio.sleep(3600)

    def cancel(self, state, generation_id):
        return False


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
    with pytest.raises(ValueError):
        Settings(ollama_base_url=url)


def test_settings_accept_local_llm_endpoints():
    assert Settings(llm_base_url="http://model:8080/").llm_base_url == "http://model:8080"
    assert Settings(llm_base_url="http://host.docker.internal:8080").llm_base_url.endswith(":8080")
    assert Settings(ollama_base_url="http://host.docker.internal:11434/").ollama_base_url.endswith(
        ":11434"
    )


def test_settings_never_allow_more_than_50_jira_comments():
    assert Settings(jira_max_comments=50).jira_max_comments == 50
    with pytest.raises(ValueError):
        Settings(jira_max_comments=51)


def test_every_session_uses_fixed_default_mapping():
    store = SessionStore(hmac_key=b"fixed-test-hmac-key")

    assert store.get("session-one").mapping == FieldMapping()
    assert store.get("session-two").mapping == FieldMapping()


def test_http_security_headers_csrf_and_session(valid_result, tmp_path):
    app = create_app(
        Settings(production=False, data_dir=tmp_path), HTTPFakeService(valid_result)
    )
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert 'value="https://project.gpb.tech/jira"' in index.text
        assert "Рабочая Jira — project.gpb.tech/jira" in index.text
        assert "Демо Jira — вымышленные данные" in index.text
        assert "Другой адрес Jira" in index.text
        assert "Проверка полей Jira" not in index.text
        assert 'data-target="mapping"' not in index.text
        assert 'textarea id="editable-result"' in index.text
        assert 'id="generation-result-notice"' in index.text
        assert "Копировать текст" in index.text
        assert "Скачать .md" in index.text
        app_js = client.get("/static/app.js")
        assert app_js.status_code == 200
        assert "navigator.clipboard.writeText(field.value)" in app_js.text
        assert "generationResultNotice(`Сценарий готов за" in app_js.text
        assert "new Blob([$('#editable-result').value]" in app_js.text
        assert "/api/fields" not in app_js.text
        assert "/api/mapping" not in app_js.text
        assert "/status" in app_js.text
        assert "/result" in app_js.text
        assert "await api('/api/context')" in app_js.text
        assert "sessionStorage.setItem(generationStorageKey" in app_js.text
        assert "trackGeneration(pending,Date.now())" not in app_js.text
        assert "serverElapsedSeconds=Number(initialElapsedSeconds)||0" in app_js.text
        assert "syncElapsed(job)" in app_js.text
        assert "generationUnavailable=x.code==='GENERATION_NOT_FOUND'" in app_js.text
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
        assert client.get("/api/context").status_code == 409
        loaded_context = client.post(
            "/api/employees/EMP-1/load?include_display_name=false",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        assert loaded_context.status_code == 200
        restored_context = client.get("/api/context")
        assert restored_context.status_code == 200
        assert restored_context.json() == loaded_context.json()
        assert client.get("/api/fields").status_code == 404
        assert client.put(
            "/api/mapping",
            headers={"X-CSRF-Token": csrf},
            json=FieldMapping().model_dump(),
        ).status_code == 404
        default_runtime = client.get("/api/model/diagnostics")
        assert default_runtime.json()["runtime"] == "llama_cpp"
        runtime_switch_is_not_exposed = client.put(
            "/api/model/runtime",
            headers={"X-CSRF-Token": csrf},
            json={"runtime": "ollama"},
        )
        assert runtime_switch_is_not_exposed.status_code == 404
        started = client.post(
            "/api/generate",
            headers={"X-CSRF-Token": csrf},
            json={"excluded_source_refs": [], "include_display_name": False},
        )
        assert started.status_code == 202
        generation_id = started.json()["generation_id"]
        assert started.json()["status"] == "running"
        for _ in range(50):
            job_status = client.get(f"/api/generate/{generation_id}/status")
            assert job_status.status_code == 200
            if job_status.json()["status"] != "running":
                break
            time.sleep(0.01)
        assert job_status.json()["status"] == "succeeded"
        completed = client.get(f"/api/generate/{generation_id}/result")
        assert completed.status_code == 200
        assert completed.json()["result"] == valid_result
        assert client.get("/api/generate/unknown/status").status_code == 404
        client_supplied_generation_id = client.post(
            "/api/generate",
            headers={"X-CSRF-Token": csrf},
            json={
                "generation_id": "client-controlled-id",
                "excluded_source_refs": [],
                "include_display_name": False,
            },
        )
        assert client_supplied_generation_id.status_code == 422
        deleted = client.delete("/api/session", headers={"X-CSRF-Token": csrf})
        assert deleted.status_code == 200


def test_background_generation_can_be_cancelled(valid_result, tmp_path):
    app = create_app(
        Settings(production=False, data_dir=tmp_path),
        HTTPBlockingFakeService(valid_result),
    )
    with TestClient(app) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        started = client.post(
            "/api/generate",
            headers={"X-CSRF-Token": csrf},
            json={"excluded_source_refs": [], "include_display_name": False},
        )
        generation_id = started.json()["generation_id"]

        cancelled = client.post(
            f"/api/generate/{generation_id}/cancel",
            headers={"X-CSRF-Token": csrf},
            json={},
        )

        assert cancelled.status_code == 200
        assert cancelled.json()["cancelled"] is True
        assert cancelled.json()["status"] == "cancelled"
        status = client.get(f"/api/generate/{generation_id}/status")
        assert status.json()["status"] == "cancelled"
        result = client.get(f"/api/generate/{generation_id}/result")
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "GENERATION_CANCELLED"
