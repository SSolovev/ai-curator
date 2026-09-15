from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .comment_analysis import (
    apply_comment_extraction,
    build_comment_extraction_messages,
    comment_extraction_schema,
    split_comment_batches,
)
from .config import Settings
from .domain import FieldMapping, ModelRuntime
from .errors import CuratorError
from .external_llm import build_external_runtime
from .jira import JiraClient, JiraTransportConfig, validate_employee_key
from .llm import LlamaCppClient, LocalLLM, OllamaClient
from .normalization import MM_FIELDS, apply_exclusions, normalize_bundle
from .privacy_filter import PersonalDataFilter, PrivacyFilterSession
from .prompting import build_messages
from .security import get_or_create_session_hmac_key, read_secret_file
from .validation import finalize_computed_fields, load_schema, validate_result

logger = logging.getLogger("ai_curator.generation")


GenerationStatus = Literal["running", "succeeded", "failed", "cancelled"]


@dataclass
class GenerationJob:
    generation_id: str
    status: GenerationStatus = "running"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    error_status_code: int = 500
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def status_payload(self) -> dict[str, Any]:
        finished_at = self.finished_at or time.monotonic()
        payload: dict[str, Any] = {
            "generation_id": self.generation_id,
            "status": self.status,
            "elapsed_seconds": round(finished_at - self.started_at, 1),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass
class SessionState:
    jira_config: JiraTransportConfig | None = None
    mapping: FieldMapping = field(default_factory=FieldMapping)
    context: Any | None = None
    selected_key: str | None = None
    hmac_key: bytes = field(default_factory=lambda: get_or_create_session_hmac_key())
    cancel_events: dict[str, asyncio.Event] = field(default_factory=dict)
    generation_jobs: dict[str, GenerationJob] = field(default_factory=dict)

    def clear(self) -> None:
        cancellable_generation_ids = set(self.cancel_events)
        for event in self.cancel_events.values():
            event.set()
        for generation_id, job in self.generation_jobs.items():
            if (
                generation_id not in cancellable_generation_ids
                and job.task is not None
                and not job.task.done()
            ):
                job.task.cancel()
        self.jira_config = None
        self.context = None
        self.selected_key = None
        self.cancel_events.clear()
        self.generation_jobs.clear()


class SessionStore:
    def __init__(self, hmac_key: bytes | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._hmac_key = hmac_key

    def get(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(
                mapping=FieldMapping(),
                hmac_key=self._hmac_key or get_or_create_session_hmac_key(),
            )
        return self._sessions[session_id]

    def delete(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state:
            state.clear()


class CuratorService:
    def __init__(
        self,
        settings: Settings,
        root: Path,
        llm: LocalLLM | None = None,
        privacy_filter: PersonalDataFilter | None = None,
    ):
        self.settings = settings
        self.root = root
        self.schema = load_schema(root / "schemas/one_on_one.response.schema.v1.json")
        # The model emits only analytical fields. The service adds deterministic
        # data_quality metadata before validating the complete API response.
        self.model_schema = deepcopy(self.schema)
        self.model_schema["required"] = [
            field_name for field_name in self.model_schema["required"] if field_name != "data_quality"
        ]
        self.model_schema["properties"].pop("data_quality", None)
        model_agenda_item = self.model_schema["properties"]["agenda"]["items"]
        model_agenda_item["required"] = [
            field_name
            for field_name in model_agenda_item["required"]
            if field_name not in {"start_minute", "end_minute"}
        ]
        model_agenda_item["properties"].pop("start_minute", None)
        model_agenda_item["properties"].pop("end_minute", None)
        self.system_prompt = root / "prompts/one_on_one.system.txt"
        self.comment_extraction_prompt = root / "prompts/comment_extraction.system.txt"
        self.runtime: ModelRuntime = settings.llm_runtime
        self.privacy_filter = privacy_filter
        if llm is not None:
            if self.runtime == "external" and self.privacy_filter is None:
                raise ValueError("external runtime cannot be constructed without a privacy filter")
            self.llm = llm
        elif self.runtime == "llama_cpp":
            self.llm = LlamaCppClient(
                settings.llm_base_url,
                settings.llm_model,
                settings.llm_timeout_seconds,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                log_request_response=settings.llm_log_request_response,
            )
        elif self.runtime == "ollama":
            self.llm = OllamaClient(
                settings.ollama_base_url,
                settings.ollama_model,
                settings.llm_timeout_seconds,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                reasoning_effort=settings.llm_reasoning_effort,
                log_request_response=settings.llm_log_request_response,
            )
        else:
            external = build_external_runtime(settings, root)
            self.llm = external.llm
            self.privacy_filter = external.privacy_filter
        self.generation_limit = asyncio.Semaphore(1)

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
        diagnostics = {
            "runtime": self.runtime,
            "runtime_label": runtime_labels[self.runtime],
            "configured_model": configured_models[self.runtime],
            "health": health,
            "contains_personal_data": False,
        }
        if self.privacy_filter is not None:
            diagnostics["privacy_filter"] = {
                "enabled": True,
                "model": self.settings.pii_filter_model,
                "health": await self.privacy_filter.health(),
                "dictionary_filter": True,
                "fail_closed": True,
            }
        return diagnostics

    def jira(self, state: SessionState) -> JiraClient:
        if not state.jira_config:
            raise CuratorError("JIRA_NOT_CONNECTED", "Сначала подключитесь к Jira.", 409)
        return JiraClient(state.jira_config)

    async def connect(
        self, state: SessionState, *, base_url: str, token: str, ca_bundle_path: str | None, proxy_url: str | None
    ) -> dict[str, Any]:
        actual_token = token.strip() or read_secret_file(self.settings.token_file)
        if not actual_token:
            raise CuratorError("JIRA_TOKEN_REQUIRED", "Введите Jira PAT или подключите Docker secret.")
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
        )
        async with JiraClient(config) as jira:
            profile = await jira.myself()
        state.jira_config = config
        # Do not retain or return identifying profile fields; confirmation is enough.
        return {"ok": True, "authenticated": bool(profile)}

    async def employees(self, state: SessionState, query: str | None, billable_only: bool) -> list[dict[str, Any]]:
        async with self.jira(state) as jira:
            issues = await jira.employee_list(billable_only=billable_only, query=query, page_size=self.settings.jira_page_size)
        return [
            {
                "key": issue.get("key"),
                "summary": issue.get("fields", {}).get("summary"),
                "status": (issue.get("fields", {}).get("status") or {}).get("name"),
            }
            for issue in issues
        ]

    async def load_employee(self, state: SessionState, key: str, include_display_name: bool = False) -> dict[str, Any]:
        safe_key = validate_employee_key(key)
        mapping_fields = [value for value in state.mapping.model_dump().values() if value]
        employee_fields = sorted(set(["summary", "status", "issuetype", *mapping_fields]))
        child_fields = sorted(
            set(
                [
                    "summary",
                    "status",
                    "created",
                    "updated",
                    "issuetype",
                    "description",
                    "customfield_11418",
                    *MM_FIELDS.values(),
                    *([state.mapping.feedback_comments] if state.mapping.feedback_comments else []),
                ]
            )
        )
        async with self.jira(state) as jira:
            employee = await jira.employee_issue(safe_key, employee_fields)
            optional = await asyncio.gather(
                jira.comments(safe_key, limit=self.settings.jira_max_comments),
                jira.child_issues(
                    safe_key,
                    fields=child_fields,
                    page_size=self.settings.jira_page_size,
                ),
                return_exceptions=True,
            )
        optional_warnings: list[str] = []
        if isinstance(optional[0], Exception):
            comments = []
            optional_warnings.append("Комментарии Jira недоступны и не были обработаны.")
        else:
            comments = optional[0]
        if isinstance(optional[1], Exception):
            children = []
            optional_warnings.append("Mood Monitoring и feedback недоступны и не были обработаны.")
        else:
            children = optional[1]
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
        state.selected_key = safe_key
        preview = context.model_copy(deep=True)
        if not include_display_name:
            preview.employee.display_name = None
        return preview.model_dump(mode="json")

    async def generate(self, state: SessionState, generation_id: str, excluded_refs: set[str], include_display_name: bool) -> dict[str, Any]:
        if state.context is None:
            raise CuratorError("CONTEXT_REQUIRED", "Сначала загрузите и проверьте данные сотрудника.", 409)
        runtime = self.runtime
        llm = self.llm
        preparation_started = time.monotonic()
        try:
            context = apply_exclusions(state.context, excluded_refs)
            # External mode always removes the name, but keeps it locally long
            # enough to add it to the deterministic per-request dictionary.
            if not include_display_name and self.privacy_filter is None:
                context.employee.display_name = None
            validation_context_dict = context.model_dump(mode="json")
            cancel_event = asyncio.Event()
            state.cancel_events[generation_id] = cancel_event
        finally:
            logger.info(
                "llm_data_preparation_completed",
                extra={"duration_ms": round((time.monotonic() - preparation_started) * 1000)},
            )
        try:
            if self.generation_limit.locked():
                raise CuratorError(
                    "LLM_BUSY",
                    "Другая генерация ещё выполняется. Отмените её или дождитесь завершения.",
                    409,
                )
            async with self.generation_limit:
                privacy_session: PrivacyFilterSession | None = None
                if self.privacy_filter is not None:
                    privacy_session = await self.privacy_filter.sanitize_context(
                        context, cancel_event
                    )
                    context = privacy_session.context
                context = await self._extract_comment_context(context, cancel_event)
                context_dict = context.model_dump(mode="json")
                messages = build_messages(context_dict, self.system_prompt)
                model_started = time.monotonic()
                try:
                    raw_result = await llm.generate(messages, self.model_schema, cancel_event)
                finally:
                    logger.info(
                        "llm_model_response_completed",
                        extra={
                            "operation": "generate",
                            "runtime": runtime,
                            "duration_ms": round((time.monotonic() - model_started) * 1000),
                        },
                    )
                try:
                    validation_result = self._restore_external_source_refs(
                        raw_result, privacy_session
                    )
                    return self._finalize_and_validate(
                        validation_result,
                        validation_context_dict,
                        operation="generate",
                    )
                except CuratorError as first_error:
                    if not self.settings.llm_repair_enabled:
                        raise first_error
                    repair = getattr(llm, "repair", None)
                    if repair is None:
                        raise first_error
                    model_started = time.monotonic()
                    try:
                        repaired_result = await repair(messages, raw_result, self.model_schema, cancel_event)
                    finally:
                        logger.info(
                            "llm_model_response_completed",
                            extra={
                                "operation": "repair",
                                "runtime": runtime,
                                "duration_ms": round((time.monotonic() - model_started) * 1000),
                            },
                        )
                    validation_result = self._restore_external_source_refs(
                        repaired_result, privacy_session
                    )
                    return self._finalize_and_validate(
                        validation_result,
                        validation_context_dict,
                        operation="repair",
                    )
        finally:
            state.cancel_events.pop(generation_id, None)

    def _restore_external_source_refs(
        self,
        result: dict[str, Any],
        privacy_session: PrivacyFilterSession | None,
    ) -> dict[str, Any]:
        if self.privacy_filter is None or privacy_session is None:
            return result
        restored = self.privacy_filter.restore_source_refs(result, privacy_session)
        if not isinstance(restored, dict):
            raise CuratorError(
                "LLM_SCHEMA_INVALID", "Модель вернула объект неизвестного формата.", 502
            )
        return restored

    async def _extract_comment_context(self, context: Any, cancel_event: asyncio.Event) -> Any:
        extractor = getattr(self.llm, "extract_comment_events", None)
        if not callable(extractor) or not context.interactions:
            return context

        batches = split_comment_batches(
            context.interactions,
            max_items=self.settings.llm_comment_batch_size,
            max_chars=self.settings.llm_comment_batch_chars,
        )
        transformed = []
        fallback_count = 0
        for batch in batches:
            extraction_started = time.monotonic()
            try:
                source_refs = [item.source_ref for item in batch]
                messages = build_comment_extraction_messages(
                    batch, self.comment_extraction_prompt
                )
                result = await extractor(
                    messages,
                    comment_extraction_schema(source_refs),
                    cancel_event,
                )
                processed, extracted_count = apply_comment_extraction(batch, result)
            except CuratorError as exc:
                if exc.code == "GENERATION_CANCELLED":
                    raise
                logger.warning("comment_extraction_failed", extra={"code": exc.code})
                processed = [item.model_copy(deep=True) for item in batch]
                extracted_count = 0
            finally:
                logger.info(
                    "llm_model_response_completed",
                    extra={
                        "operation": "comment_extraction",
                        "runtime": self.runtime,
                        "duration_ms": round(
                            (time.monotonic() - extraction_started) * 1000
                        ),
                    },
                )
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

    def _finalize_and_validate(
        self,
        raw_result: dict[str, Any],
        context: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any]:
        postprocessing_started = time.monotonic()
        try:
            finalized_result = finalize_computed_fields(raw_result, context)
        finally:
            logger.info(
                "llm_postprocessing_completed",
                extra={
                    "operation": operation,
                    "duration_ms": round((time.monotonic() - postprocessing_started) * 1000),
                },
            )

        validation_started = time.monotonic()
        try:
            return validate_result(finalized_result, context, self.schema)
        finally:
            logger.info(
                "llm_validation_completed",
                extra={
                    "operation": operation,
                    "duration_ms": round((time.monotonic() - validation_started) * 1000),
                },
            )

    def cancel(self, state: SessionState, generation_id: str) -> bool:
        event = state.cancel_events.get(generation_id)
        if event:
            event.set()
            return True
        return False


def markdown_export(result: dict[str, Any]) -> str:
    def safe(value: Any) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    def evidence(items: list[dict[str, Any]]) -> str:
        return ", ".join(f"{safe(item['date'])} — `{safe(item['source_ref'])}`" for item in items) or "не указан"

    history = result["history_summary"]
    lines = ["# Сценарий встречи 1:1", "", "## 1. Краткий анализ истории", "", safe(history["overall_dynamics"])]
    history_labels = {
        "recurring_topics": "Повторяющиеся темы",
        "positive_changes": "Положительные изменения",
        "unresolved_questions": "Нерешённые вопросы",
        "signals_to_verify": "Сигналы для проверки",
    }
    for key, label in history_labels.items():
        lines.extend(["", f"### {label}", ""])
        for item in history[key]:
            lines.append(
                f"- {safe(item['statement'])} ({safe(item['certainty'])}). Проверить: {safe(item['verify_question'])}. "
                f"Источники: {evidence(item['evidence'])}."
            )
    lines.extend(["", "## 2. Идеи для начала разговора", ""])
    lines.extend(f"- {safe(item['phrase'])}" for item in result["conversation_openers"])
    lines.extend(["", "## 3. Приоритеты встречи", ""])
    lines.extend(
        f"{item['priority']}. **{safe(item['topic'])}** — {safe(item['why_now'])}. Источники: {evidence(item['evidence'])}."
        for item in result["meeting_priorities"]
    )
    lines.extend(["", "## 4. Темы и осторожные формулировки", ""])
    lines.extend(
        f"- **{safe(item['topic'])}**. Не использовать: «{safe(item['avoid'])}». Вместо этого: «{safe(item['use_instead'])}». "
        f"Когда поднять: {safe(item['when_to_raise'])}."
        for item in result["sensitive_topics"]
    )
    lines.extend(["", "## 5. Скрипт 30-минутной встречи", "", "| Время | Цель этапа | Реплики куратора | Основные вопросы | Сигналы |", "|---|---|---|---|---|"])
    for item in result["agenda"]:
        questions = "<br>".join(
            safe(question["question"])
            + (f" ↳ {safe(question['follow_up'])}" if question["follow_up"] else "")
            for question in item["key_questions"]
        )
        lines.append(
            f"| {item['start_minute']}–{item['end_minute']} | {safe(item['goal'])} | {'<br>'.join(safe(x) for x in item['curator_lines'])} | "
            f"{questions} | "
            f"{'<br>'.join(safe(x) for x in item['listen_for'])} |"
        )
    cheat = result["curator_cheat_sheet"]
    lines.extend(["", "## 6. Итоговая памятка куратору", "", "### Три сигнала", ""])
    lines.extend(f"- {safe(item)}" for item in cheat["three_signals"])
    lines.extend(["", "### Три желаемых исхода", ""])
    lines.extend(f"- {safe(item)}" for item in cheat["three_desired_outcomes"])
    lines.extend(["", "### Проекты договорённостей", ""])
    lines.extend(
        f"- {safe(item['action'])}; владелец: {safe(item['owner'])}; срок: {safe(item['due'])}."
        for item in cheat["suggested_agreements"]
    )
    lines.extend(["", "### Сообщение после встречи", "", safe(cheat["follow_up_message_template"])])
    quality = result["data_quality"]
    if quality["context_cut"] or quality["warnings"]:
        lines.extend(["", "### Качество данных", ""])
        if quality["context_cut"]:
            lines.append("- Контекст был усечён по детерминированной политике отбора.")
        lines.extend(f"- {safe(item)}" for item in quality["warnings"])
    return "\n".join(lines) + "\n"
