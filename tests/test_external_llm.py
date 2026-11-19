from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from ai_curator.config import Settings
from ai_curator.errors import CuratorError
from ai_curator.external_llm import ExternalOpenAIClient
from ai_curator.llm import LlamaCppClient
from ai_curator.privacy_filter import PersonalDataFilter, load_dictionary
from ai_curator.service import CuratorService
from ai_curator.session import SessionState

ROOT = Path(__file__).parents[1]


class DetectingPiiModel:
    def __init__(self) -> None:
        self.calls = 0

    async def health(self):
        return {"ok": True}

    async def generate(self, messages, schema, cancel_event):
        self.calls += 1
        payload = messages[1]["content"].split("<records>\n", 1)[1].split("\n</records>", 1)[0]
        records = json.loads(payload)
        matches = []
        for record in records:
            if "Мария Иванова" in record["text"]:
                matches.append(
                    {
                        "record_id": record["record_id"],
                        "text": "Мария Иванова",
                        "category": "PERSON",
                    }
                )
        return {"matches": matches}


class NeverCalledExternalLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def health(self):
        return {"ok": True}

    async def generate(self, messages, schema, cancel_event):
        self.calls += 1
        return {}


class CapturingBadExternalLlm(NeverCalledExternalLlm):
    def __init__(self) -> None:
        super().__init__()
        self.messages = []

    async def extract_comment_events(self, messages, schema, cancel_event):
        self.messages.append(messages)
        source_refs = schema["properties"]["records"]["items"]["properties"]["source_ref"]["enum"]
        return {
            "records": [
                {
                    "source_ref": source_ref,
                    "summary": "Обезличенное рабочее событие.",
                    "topics": [],
                    "agreement": None,
                    "signal": None,
                    "open_question": None,
                }
                for source_ref in source_refs
            ]
        }

    async def generate(self, messages, schema, cancel_event):
        self.calls += 1
        self.messages.append(messages)
        return {}


class CapturingValidLlm(NeverCalledExternalLlm):
    def __init__(self, result) -> None:
        super().__init__()
        self.result = result
        self.messages = []
        self.schema = None

    async def generate(self, messages, schema, cancel_event):
        self.calls += 1
        self.messages = messages
        self.schema = schema
        return json.loads(json.dumps(self.result, ensure_ascii=False))


class FailingPrivacyFilter:
    async def health(self):
        return {"ok": False}

    async def sanitize_context(self, context, cancel_event):
        raise CuratorError("PII_FILTER_FAILED", "filter failed", 502)


class FakeExternalHttpClient:
    health_headers = None
    request_headers = None
    request_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, headers):
        type(self).health_headers = headers
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": []})

    async def post(self, url, headers, json):
        type(self).request_headers = headers
        type(self).request_payload = json
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
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )


class FakeMainHttpClient:
    result = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(type(self).result, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )


def test_external_runtime_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        Settings(llm_runtime="external")

    settings = Settings(llm_runtime="external", external_llm_enabled=True)
    assert settings.llm_runtime == "external"


@pytest.mark.parametrize(
    ("field_name", "path"),
    [
        ("external_llm_api_key_file", "/run/api_key"),
        ("external_llm_api_key_file", "/secrets/../api_key"),
        ("pii_filter_dictionary_file", "/run/pii_dictionary.txt"),
    ],
)
def test_external_runtime_requires_mounted_secret_paths(field_name, path):
    with pytest.raises(ValueError):
        Settings(
            llm_runtime="external",
            external_llm_enabled=True,
            **{field_name: path},
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1",
        "https://user:secret@api.openai.com/v1",
        "https://api.openai.com/v1?key=secret",
    ],
)
def test_external_endpoint_requires_clean_https_url(url):
    with pytest.raises(ValueError):
        Settings(external_llm_base_url=url)


def test_dictionary_loader_requires_nonempty_file(tmp_path):
    with pytest.raises(CuratorError) as missing:
        load_dictionary(tmp_path / "missing.txt")
    assert missing.value.code == "PII_DICTIONARY_MISSING"

    empty = tmp_path / "empty.txt"
    empty.write_text("# comment only\n", encoding="utf-8")
    with pytest.raises(CuratorError) as empty_error:
        load_dictionary(empty)
    assert empty_error.value.code == "PII_DICTIONARY_EMPTY"


def test_privacy_filter_uses_model_dictionary_regex_and_source_aliases(normalized, tmp_path):
    context = normalized.model_copy(deep=True)
    context.interactions[
        0
    ].text = "Мария Иванова обсуждает Проект Альфа; maria@example.com; +7 999 123-45-67."
    context.context_selection.excluded_source_refs.append("jira-comment:secret-person-42")
    original_ref = context.interactions[0].source_ref
    original_employee_ref = context.employee.employee_ref
    original_jira_key = context.employee.jira_key
    original_display_name = context.employee.display_name
    dictionary = tmp_path / "pii_dictionary.txt"
    dictionary.write_text("Проект Альфа\n", encoding="utf-8")
    model = DetectingPiiModel()
    privacy_filter = PersonalDataFilter(
        model,
        load_dictionary(dictionary),
        ROOT / "prompts/pii_filter.system.txt",
    )

    session = asyncio.run(privacy_filter.sanitize_context(context, asyncio.Event()))
    rendered = json.dumps(session.context.model_dump(mode="json"), ensure_ascii=False)

    assert model.calls >= 1
    assert "Мария Иванова" not in rendered
    assert "Проект Альфа" not in rendered
    assert "maria@example.com" not in rendered
    assert "+7 999 123-45-67" not in rendered
    assert original_employee_ref not in rendered
    assert original_jira_key not in rendered
    assert original_display_name not in rendered
    assert original_ref not in rendered
    assert "jira-comment:secret-person-42" not in rendered
    assert "[PII_PERSON]" in rendered
    assert "[PII_DICTIONARY]" in rendered
    assert "[PII_EMAIL]" in rendered
    assert "[PII_PHONE]" in rendered

    alias = next(
        alias
        for alias, source_ref in session.source_ref_restore.items()
        if source_ref == original_ref
    )
    restored = privacy_filter.restore_source_refs({"evidence": [{"source_ref": alias}]}, session)
    assert restored["evidence"][0]["source_ref"] == original_ref


def test_organization_matches_are_combined_before_sending_to_external_model(normalized, valid_result):
    class OrganizationDetector:
        async def generate(self, messages, schema, cancel_event):
            categories = schema["properties"]["matches"]["items"]["properties"]["category"]["enum"]
            assert "ORGANIZATION" in categories
            payload = messages[1]["content"].split("<records>\n", 1)[1].split("\n</records>", 1)[0]
            records = json.loads(payload)
            assert normalized.context_selection.policy not in {record["text"] for record in records}
            return {
                "matches": [
                    {"record_id": record["record_id"], "text": text, "category": "ORGANIZATION"}
                    for record in records
                    for text in ("Контур Север", "Макет", "demo")
                    if text in record["text"]
                ]
            }

    normalized.interactions[0].text = (
        "SRC-1234. Обсудили ПАО «Подрядчик Макет» и Контур Север. "
        "Алиасы ИТ9, IT_NINE, demo.invalid. Почта name@demo.invalid."
    )
    privacy_filter = PersonalDataFilter(
        OrganizationDetector(),
        ("ит9", "it_nine", "demo.invalid"),
        ROOT / "prompts/pii_filter.system.txt",
    )
    external = CapturingValidLlm(valid_result)
    service = CuratorService(
        Settings(llm_runtime="external", external_llm_enabled=True),
        ROOT,
        llm=external,
        privacy_filter=privacy_filter,
    )

    asyncio.run(service.generate(SessionState(context=normalized), "organization-redaction", set()))

    assert (
        "SRC-1234. Обсудили [PII_ORGANIZATION] и [PII_ORGANIZATION]. "
        "Алиасы [PII_DICTIONARY], [PII_DICTIONARY], [PII_DICTIONARY]. Почта [PII_EMAIL]."
        in external.messages[1]["content"]
    )
    assert normalized.context_selection.policy in external.messages[1]["content"]


def test_invalid_organization_span_is_logged_and_blocks_external_request(normalized, caplog):
    class InvalidSpanDetector:
        async def generate(self, messages, schema, cancel_event):
            record_id = schema["properties"]["matches"]["items"]["properties"]["record_id"]["enum"][0]
            return {
                "matches": [
                    {"record_id": record_id, "text": "Ошибка Макет", "category": "ORGANIZATION"}
                ]
            }

    privacy_filter = PersonalDataFilter(
        InvalidSpanDetector(),
        ("Проект Альфа",),
        ROOT / "prompts/pii_filter.system.txt",
    )
    external = NeverCalledExternalLlm()
    service = CuratorService(
        Settings(llm_runtime="external", external_llm_enabled=True),
        ROOT,
        llm=external,
        privacy_filter=privacy_filter,
    )

    with pytest.raises(CuratorError) as error:
        asyncio.run(service.generate(SessionState(context=normalized), "invalid-pii-span", set()))

    assert error.value.code == "PII_FILTER_FAILED"
    assert external.calls == 0
    event = next(record for record in caplog.records if record.msg == "pii_filter_failed")
    assert event.code == "PII_FILTER_SPAN_MISMATCH"
    assert not hasattr(event, "request_body")
    assert not hasattr(event, "response_body")


@pytest.mark.parametrize("runtime", ["llama_cpp", "ollama", "external"])
def test_main_llm_is_not_called_when_privacy_filter_fails(normalized, runtime):
    external = NeverCalledExternalLlm()
    service = CuratorService(
        Settings(
            llm_runtime=runtime,
            external_llm_enabled=runtime == "external",
        ),
        ROOT,
        llm=external,
        privacy_filter=FailingPrivacyFilter(),
    )

    with pytest.raises(CuratorError) as caught:
        asyncio.run(
            service.generate(
                SessionState(context=normalized),
                "external-filter-failure",
                set(),
            )
        )

    assert caught.value.code == "PII_FILTER_FAILED"
    assert external.calls == 0


@pytest.mark.parametrize("runtime", ["llama_cpp", "ollama", "external"])
def test_service_sends_only_sanitized_messages_to_every_runtime(
    normalized, valid_result, tmp_path, runtime
):
    context = normalized.model_copy(deep=True)
    context.interactions[0].text = "Мария Иванова, maria@example.com, работает над Проектом Альфа."
    original_ref = context.interactions[0].source_ref
    original_name = context.employee.display_name
    original_key = context.employee.jira_key
    dictionary = tmp_path / "pii_dictionary.txt"
    dictionary.write_text("Проектом Альфа\n", encoding="utf-8")
    privacy_filter = PersonalDataFilter(
        DetectingPiiModel(),
        load_dictionary(dictionary),
        ROOT / "prompts/pii_filter.system.txt",
    )
    main_llm = CapturingValidLlm(valid_result)
    service = CuratorService(
        Settings(
            llm_runtime=runtime,
            external_llm_enabled=runtime == "external",
        ),
        ROOT,
        llm=main_llm,
        privacy_filter=privacy_filter,
    )

    result = asyncio.run(
        service.generate(
            SessionState(context=context),
            f"{runtime}-sanitized-boundary",
            set(),
        )
    )

    outbound = json.dumps(main_llm.messages, ensure_ascii=False)
    for forbidden in (
        "Мария Иванова",
        "maria@example.com",
        "Проектом Альфа",
        original_ref,
        original_name,
        original_key,
    ):
        assert forbidden not in outbound
    assert "SRC-" in outbound
    assert "[PII_PERSON]" in outbound
    context_json = main_llm.messages[1]["content"].split("<employee_context>\n", 1)[1]
    sent_context = json.loads(context_json.split("\n</employee_context>", 1)[0])
    ref_schema = main_llm.schema["$defs"]["evidence"]["properties"]["source_ref"]
    assert ref_schema["enum"] == sorted(sent_context["context_selection"]["included_source_refs"])
    assert all(ref.startswith("SRC-") for ref in ref_schema["enum"])
    assert "enum" not in service.model_schema["$defs"]["evidence"]["properties"]["source_ref"]
    assert result["agenda"][-1]["end_minute"] == 30


def test_logged_main_request_is_exactly_the_sanitized_payload(
    normalized, valid_result, tmp_path, monkeypatch, caplog
):
    context = normalized.model_copy(deep=True)
    context.interactions[0].text = "Мария Иванова, maria@example.com, работает над Проектом Альфа."
    original_name = context.employee.display_name
    original_key = context.employee.jira_key
    dictionary = tmp_path / "pii_dictionary.txt"
    dictionary.write_text("Проектом Альфа\n", encoding="utf-8")
    privacy_filter = PersonalDataFilter(
        DetectingPiiModel(),
        load_dictionary(dictionary),
        ROOT / "prompts/pii_filter.system.txt",
    )
    FakeMainHttpClient.result = valid_result
    monkeypatch.setattr("ai_curator.llm.httpx.AsyncClient", FakeMainHttpClient)
    main_llm = LlamaCppClient("http://model:8080", "test-model", log_request_response=True)
    service = CuratorService(
        Settings(llm_direct_prompt_chars=1_000_000),
        ROOT,
        llm=main_llm,
        privacy_filter=privacy_filter,
    )

    with caplog.at_level(logging.INFO):
        asyncio.run(
            service.generate(
                SessionState(context=context),
                "logged-sanitized-boundary",
                set(),
            )
        )

    events = [record.msg for record in caplog.records]
    assert events.index("pii_filter_completed") < events.index("llm_request_payload")
    request_record = next(
        record for record in caplog.records if record.msg == "llm_request_payload"
    )
    outbound = request_record.request_body
    for forbidden in (
        "Мария Иванова",
        "maria@example.com",
        "Проектом Альфа",
        original_name,
        original_key,
    ):
        assert forbidden not in outbound
    assert "[PII_PERSON]" in outbound
    assert "SRC-" in outbound


def test_service_sends_only_sanitized_messages_to_external_runtime(normalized, tmp_path):
    context = normalized.model_copy(deep=True)
    context.interactions[0].text = "Мария Иванова, maria@example.com, работает над Проектом Альфа."
    original_ref = context.interactions[0].source_ref
    original_name = context.employee.display_name
    original_key = context.employee.jira_key
    dictionary = tmp_path / "pii_dictionary.txt"
    dictionary.write_text("Проектом Альфа\n", encoding="utf-8")
    privacy_filter = PersonalDataFilter(
        DetectingPiiModel(),
        load_dictionary(dictionary),
        ROOT / "prompts/pii_filter.system.txt",
    )
    external = CapturingBadExternalLlm()
    service = CuratorService(
        Settings(
            llm_runtime="external",
            external_llm_enabled=True,
        ),
        ROOT,
        llm=external,
        privacy_filter=privacy_filter,
    )

    result = asyncio.run(
        service.generate(
            SessionState(context=context),
            "external-sanitized-boundary",
            set(),
        )
    )

    outbound = json.dumps(external.messages, ensure_ascii=False)
    for forbidden in (
        "Мария Иванова",
        "maria@example.com",
        "Проектом Альфа",
        original_ref,
        original_name,
        original_key,
    ):
        assert forbidden not in outbound
    assert "SRC-" in outbound
    assert "[PII_PERSON]" in outbound or "Обезличенное рабочее событие" in outbound
    assert "data_quality" in result


def test_external_client_uses_structured_output_and_never_logs_key(monkeypatch, caplog):
    monkeypatch.setattr("ai_curator.external_llm.httpx.AsyncClient", FakeExternalHttpClient)
    client = ExternalOpenAIClient(
        "https://api.openai.com/v1",
        "gpt-4.1-mini",
        "sk-test-secret",
        30,
        temperature=0.2,
        max_tokens=6000,
        log_request_response=True,
    )
    messages = [{"role": "user", "content": "[PERSON] обсуждает задачу"}]

    with caplog.at_level(logging.INFO, logger="ai_curator.external_llm"):
        health = asyncio.run(client.health())
        result = asyncio.run(client.generate(messages, {"type": "object"}, asyncio.Event()))

    assert health == {"ok": True, "status": 200}
    assert result == {"result": "готово"}
    assert FakeExternalHttpClient.request_headers["Authorization"] == "Bearer sk-test-secret"
    payload = FakeExternalHttpClient.request_payload
    assert payload["store"] is False
    assert payload["max_completion_tokens"] == 6000
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "reasoning_effort" not in payload
    assert "sk-test-secret" not in "\n".join(str(record.__dict__) for record in caplog.records)


def test_yandex_client_sends_the_catalog_and_key_on_both_routes(monkeypatch):
    monkeypatch.setattr("ai_curator.external_llm.httpx.AsyncClient", FakeExternalHttpClient)
    folder_id = "b1gexamplefolder"
    model_uri = f"gpt://{folder_id}/qwen3.6-35b-a3b"
    client = ExternalOpenAIClient(
        "https://ai.api.cloud.yandex.net/v1",
        model_uri,
        "test-secret",
        30,
        temperature=0.2,
        max_tokens=6000,
    )

    assert asyncio.run(client.health())["ok"]
    assert asyncio.run(client.generate([], {"type": "object"}, asyncio.Event())) == {
        "result": "готово"
    }
    assert FakeExternalHttpClient.health_headers["Authorization"] == "Api-Key test-secret"
    assert FakeExternalHttpClient.health_headers["x-project"] == folder_id
    assert FakeExternalHttpClient.request_headers["Authorization"] == "Api-Key test-secret"
    assert FakeExternalHttpClient.request_headers["OpenAI-Project"] == folder_id
    assert FakeExternalHttpClient.request_headers["x-data-logging-enabled"] == "false"
    assert FakeExternalHttpClient.request_payload["model"] == model_uri
    assert FakeExternalHttpClient.request_payload["response_format"]["type"] == "json_schema"
    assert FakeExternalHttpClient.request_payload["reasoning_effort"] == "none"


@pytest.mark.parametrize(
    ("finish_reason", "content", "expected_code"),
    [
        ("length", None, "EXTERNAL_LLM_OUTPUT_TRUNCATED"),
        ("length", '{"result":', "EXTERNAL_LLM_OUTPUT_TRUNCATED"),
        ("stop", None, "EXTERNAL_LLM_EMPTY_RESPONSE"),
        ("stop", "не JSON", "EXTERNAL_LLM_INVALID_JSON"),
    ],
)
def test_external_response_errors_distinguish_truncation_from_bad_json(
    monkeypatch, finish_reason, content, expected_code
):
    async def respond(self, url, payload, cancel_event, headers=None):
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]},
        )

    monkeypatch.setattr(ExternalOpenAIClient, "_post_completion", respond)
    client = ExternalOpenAIClient(
        "https://api.openai.com/v1",
        "test-model",
        "test-secret",
        30,
        temperature=0.2,
        max_tokens=6000,
    )
    with pytest.raises(CuratorError) as caught:
        asyncio.run(client.generate([], {"type": "object"}, asyncio.Event()))
    assert caught.value.code == expected_code


def test_yandex_client_requires_the_folder_in_the_model_uri():
    with pytest.raises(CuratorError) as error:
        ExternalOpenAIClient(
            "https://ai.api.cloud.yandex.net/v1",
            "qwen3.6-35b-a3b",
            "test-secret",
            30,
            temperature=0.2,
            max_tokens=6000,
        )
    assert error.value.code == "EXTERNAL_LLM_MODEL_INVALID"


@pytest.mark.parametrize("runtime", ["llama_cpp", "ollama", "external"])
def test_runtime_does_not_accept_unfiltered_injected_client(runtime):
    with pytest.raises(ValueError):
        CuratorService(
            Settings(
                llm_runtime=runtime,
                external_llm_enabled=runtime == "external",
            ),
            ROOT,
            llm=NeverCalledExternalLlm(),
        )
