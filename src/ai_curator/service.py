from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .domain import FieldMapping
from .errors import CuratorError
from .jira import JiraClient, JiraTransportConfig, validate_employee_key
from .llm import LlamaCppClient, LocalLLM
from .normalization import apply_exclusions, normalize_bundle
from .prompting import build_messages
from .security import get_or_create_session_hmac_key, read_secret_file
from .validation import finalize_computed_fields, load_schema, validate_result


@dataclass
class SessionState:
    jira_config: JiraTransportConfig | None = None
    mapping: FieldMapping = field(default_factory=FieldMapping)
    context: Any | None = None
    selected_key: str | None = None
    hmac_key: bytes = field(default_factory=lambda: get_or_create_session_hmac_key())
    cancel_events: dict[str, asyncio.Event] = field(default_factory=dict)

    def clear(self) -> None:
        self.jira_config = None
        self.context = None
        self.selected_key = None
        self.cancel_events.clear()


class SessionStore:
    def __init__(self, hmac_key: bytes | None = None, default_mapping: FieldMapping | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._hmac_key = hmac_key
        self._default_mapping = default_mapping or FieldMapping()

    def get(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(
                mapping=self._default_mapping.model_copy(deep=True),
                hmac_key=self._hmac_key or get_or_create_session_hmac_key(),
            )
        return self._sessions[session_id]

    def delete(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state:
            state.clear()


class CuratorService:
    def __init__(self, settings: Settings, root: Path, llm: LocalLLM | None = None):
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
        self.llm = llm or LlamaCppClient(settings.llm_base_url, settings.llm_model, settings.llm_timeout_seconds)
        self.generation_limit = asyncio.Semaphore(1)

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

    async def field_catalog(self, state: SessionState) -> dict[str, Any]:
        metadata_warnings: list[str] = []
        try:
            async with self.jira(state) as jira:
                fields = await jira.fields()
        except CuratorError as exc:
            if exc.code not in {"JIRA_HTTP_403", "JIRA_HTTP_404"}:
                raise
            fields = []
            metadata_warnings.append(
                "Каталог полей Jira недоступен. Используйте сохранённые идентификаторы или обратитесь к администратору."
            )
        safe_fields = [{"id": item.get("id"), "name": item.get("name")} for item in fields if item.get("id") and item.get("name")]
        return {"fields": safe_fields, "mapping": state.mapping.model_dump(), "warnings": metadata_warnings + self.mapping_warnings(state.mapping)}

    @staticmethod
    def mapping_warnings(mapping: FieldMapping) -> list[str]:
        return [f"Поле «{name}» не сопоставлено; будет использовано null." for name, value in mapping.model_dump().items() if not value]

    async def load_employee(self, state: SessionState, key: str, include_display_name: bool = False) -> dict[str, Any]:
        safe_key = validate_employee_key(key)
        mapping_fields = [value for value in state.mapping.model_dump().values() if value]
        base_fields = (
            ["*all"]
            if self.settings.jira_log_employee_issue
            else ["summary", "status", "issuetype", *mapping_fields]
        )
        async with self.jira(state) as jira:
            employee = await jira.employee_issue(safe_key, sorted(set(base_fields)))
            optional = await asyncio.gather(
                jira.comments(safe_key, page_size=self.settings.jira_page_size, max_pages=self.settings.jira_max_pages),
                jira.child_issues(safe_key, page_size=self.settings.jira_page_size),
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
        context.context_selection.warnings.extend(self.mapping_warnings(state.mapping) + optional_warnings)
        state.context = context
        state.selected_key = safe_key
        preview = context.model_copy(deep=True)
        if not include_display_name:
            preview.employee.display_name = None
        return preview.model_dump(mode="json")

    async def generate(self, state: SessionState, generation_id: str, excluded_refs: set[str], include_display_name: bool) -> dict[str, Any]:
        if state.context is None:
            raise CuratorError("CONTEXT_REQUIRED", "Сначала загрузите и проверьте данные сотрудника.", 409)
        context = apply_exclusions(state.context, excluded_refs)
        if not include_display_name:
            context.employee.display_name = None
        context_dict = context.model_dump(mode="json")
        cancel_event = asyncio.Event()
        state.cancel_events[generation_id] = cancel_event
        messages = build_messages(context_dict, self.system_prompt)
        try:
            async with self.generation_limit:
                raw_result = await self.llm.generate(messages, self.model_schema, cancel_event)
                try:
                    finalized_result = finalize_computed_fields(raw_result, context_dict)
                    return validate_result(finalized_result, context_dict, self.schema)
                except CuratorError as first_error:
                    if not self.settings.llm_repair_enabled:
                        raise first_error
                    repair = getattr(self.llm, "repair", None)
                    if repair is None:
                        raise first_error
                    repaired_result = await repair(messages, raw_result, self.model_schema, cancel_event)
                    finalized_repair = finalize_computed_fields(repaired_result, context_dict)
                    return validate_result(finalized_repair, context_dict, self.schema)
        finally:
            state.cancel_events.pop(generation_id, None)

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
    lines.extend(f"- {safe(item['phrase'])} — {safe(item['basis'])}" for item in result["conversation_openers"])
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
