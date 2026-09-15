from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from ai_curator.errors import CuratorError
from ai_curator.llm import LlamaCppClient, OllamaClient


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, json):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"result":"готово"}'},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )


class FakeOllamaAsyncClient(FakeAsyncClient):
    request_payload = None

    async def get(self, url):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"models": [{"name": "ai-curator-qwen3-8b:latest"}]},
        )

    async def post(self, url, json):
        type(self).request_payload = json
        return await super().post(url, json)


def test_full_llm_request_and_response_logging(monkeypatch, caplog):
    monkeypatch.setattr("ai_curator.llm.httpx.AsyncClient", FakeAsyncClient)
    client = LlamaCppClient(
        "http://model:8080",
        "test-model",
        log_request_response=True,
    )
    messages = [{"role": "user", "content": "полный контекст"}]

    with caplog.at_level(logging.INFO, logger="ai_curator.llm"):
        result = asyncio.run(
            client.generate(messages, {"type": "object"}, asyncio.Event())
        )

    assert result == {"result": "готово"}
    request_record = next(
        record for record in caplog.records if record.msg == "llm_request_payload"
    )
    response_record = next(
        record for record in caplog.records if record.msg == "llm_response_payload"
    )
    metadata_record = next(
        record for record in caplog.records if record.msg == "llm_response_metadata"
    )
    assert json.loads(request_record.request_body)["messages"] == messages
    assert (
        json.loads(response_record.response_body)["choices"][0]["message"]["content"]
        == '{"result":"готово"}'
    )
    assert request_record.operation == "generate"
    assert response_record.operation == "generate"
    assert metadata_record.finish_reason == "stop"
    assert metadata_record.prompt_tokens == 12
    assert metadata_record.completion_tokens == 8


def test_full_llm_payload_logging_is_disabled_by_default(monkeypatch, caplog):
    monkeypatch.setattr("ai_curator.llm.httpx.AsyncClient", FakeAsyncClient)
    client = LlamaCppClient("http://model:8080", "test-model")

    with caplog.at_level(logging.INFO, logger="ai_curator.llm"):
        asyncio.run(
            client.generate(
                [{"role": "user", "content": "персональные данные"}],
                {"type": "object"},
                asyncio.Event(),
            )
        )

    event_names = {record.msg for record in caplog.records}
    assert "llm_request_payload" not in event_names
    assert "llm_response_payload" not in event_names


def test_ollama_health_checks_model_and_disables_reasoning_by_default(monkeypatch):
    monkeypatch.setattr("ai_curator.llm.httpx.AsyncClient", FakeOllamaAsyncClient)
    client = OllamaClient(
        "http://host.docker.internal:11434", "ai-curator-qwen3-8b"
    )

    health = asyncio.run(client.health())
    result = asyncio.run(
        client.generate(
            [{"role": "user", "content": "контекст"}],
            {"type": "object"},
            asyncio.Event(),
        )
    )

    assert health == {"ok": True, "status": 200, "model_available": True}
    assert result == {"result": "готово"}
    assert FakeOllamaAsyncClient.request_payload["reasoning_effort"] == "none"
    assert FakeOllamaAsyncClient.request_payload["temperature"] == 0.2
    assert FakeOllamaAsyncClient.request_payload["max_tokens"] == 6000
    assert FakeOllamaAsyncClient.request_payload["response_format"]["type"] == "json_schema"


def test_ollama_omits_reasoning_effort_for_gemma3(monkeypatch):
    monkeypatch.setattr("ai_curator.llm.httpx.AsyncClient", FakeOllamaAsyncClient)
    client = OllamaClient(
        "http://host.docker.internal:11434", "ai-curator-gemma3-4b"
    )

    asyncio.run(
        client.generate(
            [{"role": "user", "content": "контекст"}],
            {"type": "object"},
            asyncio.Event(),
        )
    )

    assert "reasoning_effort" not in FakeOllamaAsyncClient.request_payload


@pytest.mark.parametrize(
    ("finish_reason", "content", "expected_code"),
    [
        ("length", "", "LLM_OUTPUT_TRUNCATED"),
        ("stop", "", "LLM_EMPTY_RESPONSE"),
        ("stop", "   ", "LLM_EMPTY_RESPONSE"),
    ],
)
def test_llm_reports_truncated_and_empty_responses(
    monkeypatch, finish_reason, content, expected_code
):
    class FakeInvalidResponseClient(FakeAsyncClient):
        async def post(self, url, json):
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "choices": [
                        {
                            "finish_reason": finish_reason,
                            "message": {"content": content},
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 6000},
                },
            )

    monkeypatch.setattr("ai_curator.llm.httpx.AsyncClient", FakeInvalidResponseClient)
    client = LlamaCppClient("http://model:8080", "test-model")

    with pytest.raises(CuratorError) as exc_info:
        asyncio.run(
            client.generate(
                [{"role": "user", "content": "контекст"}],
                {"type": "object"},
                asyncio.Event(),
            )
        )

    assert exc_info.value.code == expected_code
