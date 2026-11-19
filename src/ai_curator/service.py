from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .comment_analysis import (
    apply_comment_extraction,
    build_comment_extraction_messages,
    comment_extraction_schema,
    split_comment_batches,
)
from .config import Settings
from .domain import NormalizedContext
from .errors import CuratorError
from .external_llm import build_external_client
from .jira import JiraClient, JiraTransportConfig, validate_employee_key
from .llm import LlamaCppClient, MainLLM, OllamaClient
from .normalization import MM_FIELDS, apply_exclusions, normalize_bundle
from .privacy_filter import PersonalDataFilter, PrivacyFilterSession, build_privacy_filter
from .prompting import build_messages
from .security import read_secret_file
from .session import SessionState
from .validation import (
    finalize_computed_fields,
    load_model_schema,
    model_schema_for_context,
    validate_result,
)

logger = logging.getLogger("ai_curator.generation")


@contextmanager
def log_duration(event: str, **details: Any) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    finally:
        logger.info(
            event,
            extra={**details, "duration_ms": round((time.monotonic() - started) * 1000)},
        )


class CuratorService:
    def __init__(
        self,
        settings: Settings,
        root: Path,
        llm: MainLLM | None = None,
        privacy_filter: PersonalDataFilter | None = None,
    ):
        self.settings = settings
        self.model_schema = load_model_schema(root / "schemas/one_on_one.response.schema.v1.json")
        self.system_prompt = root / "prompts/one_on_one.system.txt"
        self.comment_extraction_prompt = root / "prompts/comment_extraction.system.txt"
        self.runtime = settings.llm_runtime

        if llm is not None and privacy_filter is None:
            raise ValueError("a main runtime cannot be constructed without a privacy filter")
        self.privacy_filter = (
            privacy_filter if privacy_filter is not None else build_privacy_filter(settings, root)
        )
        self.llm = llm if llm is not None else self._build_llm()
        self.generation_limit = asyncio.Semaphore(1)

    def _build_llm(self) -> MainLLM:
        settings = self.settings
        if self.runtime == "external":
            return build_external_client(settings)
        if self.runtime == "ollama":
            return OllamaClient(
                settings.ollama_base_url,
                settings.ollama_model,
                settings.llm_timeout_seconds,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                reasoning_effort=settings.llm_reasoning_effort,
                log_request_response=settings.llm_log_request_response,
            )
        return LlamaCppClient(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            log_request_response=settings.llm_log_request_response,
        )

    async def model_diagnostics(self) -> dict[str, Any]:
        health = await self.llm.health()
        configured_models = {
            "llama_cpp": self.settings.llm_model,
            "ollama": self.settings.ollama_model,
            "external": self.settings.external_llm_model,
        }
        runtime_labels = {
            "llama_cpp": "llama.cpp (Docker)",
            "ollama": "Ollama (Native)",
            "external": "Внешняя LLM",
        }
        return {
            "runtime": self.runtime,
            "runtime_label": runtime_labels[self.runtime],
            "configured_model": configured_models[self.runtime],
            "health": health,
            "contains_personal_data": False,
            "privacy_filter": await self.privacy_filter_diagnostics(),
        }

    async def privacy_filter_diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "required_for_all_runtimes": True,
            "model": self.settings.pii_filter_model,
            "health": await self.privacy_filter.health(),
            "dictionary_filter": True,
            "fail_closed": True,
        }

    def jira(self, state: SessionState) -> JiraClient:
        if not state.jira_config:
            raise CuratorError("JIRA_NOT_CONNECTED", "Сначала подключитесь к Jira.", 409)
        return JiraClient(state.jira_config)

    async def connect(
        self,
        state: SessionState,
        *,
        base_url: str,
        token: str,
        ca_bundle_path: str | None,
        proxy_url: str | None,
    ) -> dict[str, Any]:
        actual_token = token.strip() or read_secret_file(self.settings.token_file)
        if not actual_token:
            raise CuratorError(
                "JIRA_TOKEN_REQUIRED", "Введите Jira PAT или подключите Docker secret."
            )
        config = JiraTransportConfig(
            base_url=base_url,
            token=actual_token,
            ca_bundle_path=ca_bundle_path,
            proxy_url=proxy_url,
            connect_timeout=self.settings.jira_connect_timeout_seconds,
            read_timeout=self.settings.jira_read_timeout_seconds,
            log_request_response=self.settings.jira_log_request_response,
            log_invalid_response_body=self.settings.jira_log_invalid_response_body,
            log_field_catalog=self.settings.jira_log_field_catalog,
            log_employee_issue=self.settings.jira_log_employee_issue,
            log_response_max_chars=self.settings.jira_log_response_max_chars,
            max_response_bytes=self.settings.max_jira_response_bytes,
            max_pages=self.settings.jira_max_pages,
        )
        async with JiraClient(config) as jira:
            profile = await jira.myself()
            if config.log_field_catalog:
                try:
                    await jira.fields()
                except CuratorError as exc:
                    logger.warning("jira_field_catalog_failed", extra={"code": exc.code})
        state.jira_config = config
        # Do not retain or return identifying profile fields; confirmation is enough.
        return {"ok": True, "authenticated": bool(profile)}

    async def employees(
        self, state: SessionState, query: str | None, billable_only: bool
    ) -> list[dict[str, Any]]:
        async with self.jira(state) as jira:
            issues = await jira.employee_list(
                billable_only=billable_only, query=query, page_size=self.settings.jira_page_size
            )
        return [
            {
                "key": issue.get("key"),
                "summary": issue.get("fields", {}).get("summary"),
                "status": (issue.get("fields", {}).get("status") or {}).get("name"),
            }
            for issue in issues
        ]

    async def load_employee(
        self, state: SessionState, key: str, include_display_name: bool = False
    ) -> dict[str, Any]:
        safe_key = validate_employee_key(key)
        mapping_fields = {value for value in state.mapping.model_dump().values() if value}
        employee_fields = sorted({"summary", "status", "issuetype"} | mapping_fields)
        child_fields = {
            "summary",
            "status",
            "created",
            "updated",
            "issuetype",
            "description",
            "customfield_11418",
            *MM_FIELDS.values(),
        }
        if state.mapping.feedback_comments:
            child_fields.add(state.mapping.feedback_comments)

        async with self.jira(state) as jira:
            employee = await jira.employee_issue(safe_key, employee_fields)
            comments, children = await asyncio.gather(
                jira.comments(safe_key, limit=self.settings.jira_max_comments),
                jira.child_issues(
                    safe_key,
                    fields=sorted(child_fields),
                    page_size=self.settings.jira_page_size,
                ),
                return_exceptions=True,
            )
        optional_warnings: list[str] = []
        if isinstance(comments, asyncio.CancelledError) or isinstance(
            children, asyncio.CancelledError
        ):
            raise asyncio.CancelledError
        if isinstance(comments, Exception):
            comments = []
            optional_warnings.append("Комментарии Jira недоступны и не были обработаны.")
        if isinstance(children, Exception):
            children = []
            optional_warnings.append("Mood Monitoring и feedback недоступны и не были обработаны.")
        context = normalize_bundle(
            employee_issue=employee,
            comments=comments,
            children=children,
            mapping=state.mapping,
            hmac_key=state.hmac_key,
            current_time=datetime.now(UTC),
            max_comment_chars=self.settings.max_comment_chars,
            max_context_chars=self.settings.max_context_chars,
            include_display_name=True,
            mood_issue_type_names=self.settings.mood_issue_type_names,
            feedback_issue_type_names=self.settings.feedback_issue_type_names,
        )
        context.context_selection.warnings.extend(optional_warnings)
        state.context = context
        preview = context.model_copy(deep=True)
        if not include_display_name:
            preview.employee.display_name = None
        return preview.model_dump(mode="json")

    async def generate(
        self, state: SessionState, generation_id: str, excluded_refs: set[str]
    ) -> dict[str, Any]:
        if state.context is None:
            raise CuratorError(
                "CONTEXT_REQUIRED", "Сначала загрузите и проверьте данные сотрудника.", 409
            )

        with log_duration("llm_data_preparation_completed"):
            context = apply_exclusions(state.context, excluded_refs)
            # The privacy filter uses the local employee name to remove it from the text.
            validation_context = context.model_dump(mode="json")

        if self.generation_limit.locked():
            raise CuratorError(
                "LLM_BUSY",
                "Другая генерация ещё выполняется. Отмените её или дождитесь завершения.",
                409,
            )

        cancel_event = asyncio.Event()
        state.cancel_events[generation_id] = cancel_event
        try:
            async with self.generation_limit:
                return await self._generate_result(context, validation_context, cancel_event)
        finally:
            state.cancel_events.pop(generation_id, None)

    async def _generate_result(
        self,
        context: NormalizedContext,
        validation_context: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        privacy_session = await self.privacy_filter.sanitize_context(context, cancel_event)
        sanitized = privacy_session.context
        sanitized_data = sanitized.model_dump(mode="json")
        model_schema = model_schema_for_context(self.model_schema, sanitized_data)
        messages = build_messages(sanitized_data, self.system_prompt)
        schema_chars = len(json.dumps(model_schema, ensure_ascii=False, separators=(",", ":")))
        prompt_chars = sum(len(message["content"]) for message in messages) + schema_chars
        direct_prompt_limit = (
            self.settings.external_llm_direct_prompt_chars
            if self.runtime == "external"
            else self.settings.llm_direct_prompt_chars
        )
        if sanitized.interactions and prompt_chars > direct_prompt_limit:
            prepared = await self._extract_comment_context(sanitized, cancel_event)
            messages = build_messages(prepared.model_dump(mode="json"), self.system_prompt)

        with log_duration(
            "llm_model_response_completed", operation="generate", runtime=self.runtime
        ):
            raw_result = await self.llm.generate(messages, model_schema, cancel_event)

        try:
            return self._finalize_result(
                raw_result, privacy_session, validation_context, "generate"
            )
        except CuratorError:
            repair = getattr(self.llm, "repair", None)
            if not self.settings.llm_repair_enabled or not callable(repair):
                raise

        with log_duration("llm_model_response_completed", operation="repair", runtime=self.runtime):
            repaired_result = await repair(messages, raw_result, model_schema, cancel_event)
        return self._finalize_result(repaired_result, privacy_session, validation_context, "repair")

    async def _extract_comment_context(
        self, context: NormalizedContext, cancel_event: asyncio.Event
    ) -> NormalizedContext:
        extractor = getattr(self.llm, "extract_comment_events", None)
        if not callable(extractor):
            return context

        batches = split_comment_batches(
            context.interactions,
            max_chars=self.settings.llm_comment_batch_chars,
        )
        transformed = []
        fallback_count = 0
        for batch in batches:
            try:
                with log_duration(
                    "llm_model_response_completed",
                    operation="comment_extraction",
                    runtime=self.runtime,
                ):
                    messages = build_comment_extraction_messages(
                        batch, self.comment_extraction_prompt
                    )
                    schema = comment_extraction_schema([item.source_ref for item in batch])
                    result = await extractor(messages, schema, cancel_event)
                    processed, extracted_count = apply_comment_extraction(batch, result)
            except CuratorError as exc:
                if exc.code == "GENERATION_CANCELLED":
                    raise
                logger.warning("comment_extraction_failed", extra={"code": exc.code})
                processed = batch
                extracted_count = 0
            transformed.extend(processed)
            fallback_count += len(batch) - extracted_count

        prepared = context.model_copy(deep=True)
        prepared.interactions = transformed
        if fallback_count:
            prepared.context_selection.warnings.append(
                f"Для {fallback_count} комментариев предварительное извлечение событий "
                "не выполнено; итоговая модель получила их очищенный исходный текст."
            )
        return prepared

    def _finalize_result(
        self,
        raw_result: Any,
        privacy_session: PrivacyFilterSession,
        validation_context: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        restored = self.privacy_filter.restore_source_refs(raw_result, privacy_session)
        with log_duration("llm_postprocessing_completed", operation=operation):
            result = finalize_computed_fields(restored, validation_context)
        with log_duration("llm_validation_completed", operation=operation):
            return validate_result(result, validation_context)

    def cancel(self, state: SessionState, generation_id: str) -> bool:
        event = state.cancel_events.get(generation_id)
        if event:
            event.set()
            return True
        return False
