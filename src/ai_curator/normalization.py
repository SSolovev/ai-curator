from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

from .domain import (
    ContextSelection,
    Employee,
    Feedback,
    FieldMapping,
    Interaction,
    JiraRecordedRisk,
    JiraRecordedValue,
    MMChange,
    MoodMonitoring,
    MoodScores,
    NormalizedContext,
)
from .security import employee_reference

MM_FIELDS = {
    "work_life_balance": "customfield_12326",
    "work_content": "customfield_12330",
    "client_processes": "customfield_12332",
    "equipment": "customfield_12333",
    "recommendation": "customfield_12334",
    "manager_interaction": "customfield_12335",
    "help_contact": "customfield_16104",
    "it1_helps": "customfield_16105",
}
MEETING_RE = re.compile(r"\{meeting-type\}(.*?)\{meeting-type\}", re.IGNORECASE | re.DOTALL)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RELEVANCE_PATTERNS = {
    "agreement": re.compile(
        r"\b(договорил|согласовал|следующ|срок|дедлайн|action|follow.?up)\w*", re.I
    ),
    "work_signal": re.compile(
        r"\b(блокер|проблем|сложност|перегруз|поддержк|помощ|риск|конфликт)\w*", re.I
    ),
    "kpi": re.compile(r"\bKPI\b", re.I),
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_jira_text(value: Any, max_chars: int = 12_000) -> str:
    """Strip presentational markup while retaining readable content and untrusted commands."""
    if value is None:
        return ""
    text = str(value)[: max_chars * 2]
    parser = _TextExtractor()
    try:
        parser.feed(text)
        text = "".join(parser.parts)
    except Exception:
        text = html.unescape(text)
    # Common Jira wiki presentation syntax. Keep link labels and URLs readable.
    text = re.sub(r"\[([^]|]+)\|([^]]+)]", r"\1 (\2)", text)
    text = re.sub(r"\[([^]]+)]", r"\1", text)
    text = re.sub(r"(?m)^\s*h[1-6]\.\s*", "", text)
    text = re.sub(r"(?m)^\s*[#*+-]+\s+", "• ", text)
    text = re.sub(r"(?m)^\s*\|\|?", "", text)
    text = re.sub(
        r"\{(?:color(?::[^}]*)?|panel(?::[^}]*)?|quote|noformat|code(?::[^}]*)?)\}",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"(?<!\w)[*_~^]{1,2}([^\n]+?)[*_~^]{1,2}(?!\w)", r"\1", text)
    text = CONTROL_RE.sub("", html.unescape(text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return text[:max_chars]


def parse_meeting_type(value: Any, max_chars: int = 12_000) -> tuple[str | None, str | None, str]:
    raw_text = clean_jira_text(value, max_chars=max_chars)
    match = MEETING_RE.search(raw_text)
    if not match:
        return None, None, raw_text
    original = match.group(1).strip()
    normalized_map = {"👥 1-1": "1-1", "1-1": "1-1", "😊 mm": "MM", "mm": "MM", "kpi": "KPI"}
    normalized = normalized_map.get(original.casefold(), original or None)
    cleaned = MEETING_RE.sub("", raw_text).strip()
    return normalized, original or None, cleaned


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        # Jira often uses +0300 instead of +03:00; fromisoformat supports both on modern Python.
        try:
            result = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
                try:
                    result = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
            else:
                return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def parse_date(value: Any) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def _plain_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for name in ("value", "name", "displayName"):
            if value.get(name) is not None:
                return clean_jira_text(value[name], 500) or None
        return None
    return clean_jira_text(value, 500) or None


def _multi_value(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [item for item in (_plain_value(v) for v in values) if item]


def _score(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 10 else None


def _status_name(fields: dict[str, Any]) -> str | None:
    return _plain_value(fields.get("status"))


def interaction_relevance(interaction_type: str | None, text: str) -> int:
    score = 3 if (interaction_type or "").upper() in {"1-1", "MM", "KPI"} else 0
    score += sum(1 for pattern in RELEVANCE_PATTERNS.values() if pattern.search(text))
    return score


def normalize_interactions(comments: list[dict[str, Any]], max_chars: int) -> list[Interaction]:
    seen: set[str] = set()
    output: list[Interaction] = []
    for comment in comments:
        comment_id = str(comment.get("id", "")).strip()
        created = parse_datetime(comment.get("created") or comment.get("updated"))
        if not comment_id or not created:
            continue
        source_ref = f"jira-comment:{comment_id}"
        if source_ref in seen:
            continue
        seen.add(source_ref)
        meeting_type, original_type, text = parse_meeting_type(comment.get("body"), max_chars)
        if not text:
            continue
        # Account existence does not prove that the author is a curator.
        role = "unknown"
        visibility_obj = comment.get("visibility") or {}
        visibility = _plain_value(visibility_obj) or "Jira permissions"
        output.append(
            Interaction(
                source_ref=source_ref,
                date=created,
                type=meeting_type,
                original_type=original_type,
                author_role=role,
                text=text,
                visibility=visibility,
                relevance=interaction_relevance(meeting_type, text),
            )
        )
    return sorted(output, key=lambda item: (item.date, item.source_ref), reverse=True)


def normalize_children(
    issues: list[dict[str, Any]],
    max_chars: int,
    mapping: FieldMapping,
    mood_issue_type_names: list[str] | None = None,
    feedback_issue_type_names: list[str] | None = None,
) -> tuple[list[MoodMonitoring], list[Feedback]]:
    moods: list[MoodMonitoring] = []
    feedback: list[Feedback] = []
    mood_names = {name.casefold() for name in (mood_issue_type_names or ["Mood Monitoring"])}
    feedback_names = {
        name.casefold() for name in (feedback_issue_type_names or ["Feedback", "KPI"])
    }
    seen: set[str] = set()
    for issue in issues:
        key = str(issue.get("key", "")).strip()
        if not key:
            continue
        source_ref = f"jira:{key}"
        if source_ref in seen:
            continue
        seen.add(source_ref)
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        issue_type = (_plain_value(fields.get("issuetype")) or "").casefold()
        created = parse_date(fields.get("customfield_11418") or fields.get("created"))
        if issue_type in mood_names:
            status = _status_name(fields)
            status_fold = (status or "").casefold()
            closed = status_fold in {"closed", "done", "resolved", "закрыт", "выполнено", "готово"}
            scores = MoodScores(
                **{name: _score(fields.get(field_id)) for name, field_id in MM_FIELDS.items()}
            )
            moods.append(
                MoodMonitoring(
                    source_ref=source_ref,
                    period=_plain_value(fields.get("summary")),
                    date=created,
                    status=status,
                    scores=scores,
                    comment=clean_jira_text(fields.get(mapping.feedback_comments or ""), max_chars)
                    or None,
                    closed=closed,
                )
            )
        elif issue_type in feedback_names:
            text = clean_jira_text(
                fields.get(mapping.feedback_comments or "") or fields.get("description"), max_chars
            )
            if text:
                feedback.append(
                    Feedback(
                        source_ref=source_ref,
                        date=created,
                        type=_plain_value(fields.get("issuetype")),
                        text=text,
                    )
                )
    moods.sort(key=lambda item: (item.date or date.min, item.source_ref), reverse=True)
    feedback.sort(key=lambda item: (item.date or date.min, item.source_ref), reverse=True)
    return moods, feedback


def calculate_mm_dynamics(moods: list[MoodMonitoring]) -> list[MMChange]:
    comparable = sorted(
        [item for item in moods if item.closed and item.date],
        key=lambda item: item.date or date.min,
    )
    changes: list[MMChange] = []
    for previous, current in zip(comparable, comparable[1:], strict=False):
        prev_scores = previous.scores.model_dump()
        curr_scores = current.scores.model_dump()
        for metric in MM_FIELDS:
            old, new = prev_scores[metric], curr_scores[metric]
            if old is not None and new is not None:
                changes.append(
                    MMChange(
                        metric=metric,
                        from_value=old,
                        to_value=new,
                        delta=round(new - old, 2),
                        from_source_ref=previous.source_ref,
                        to_source_ref=current.source_ref,
                    )
                )
    return changes


def select_context(
    moods: list[MoodMonitoring],
    interactions: list[Interaction],
    feedback: list[Feedback],
    max_context_chars: int,
) -> tuple[
    list[MoodMonitoring], list[MoodMonitoring], list[Interaction], list[Feedback], ContextSelection
]:
    valid_closed = [m for m in moods if m.closed and m.date]
    selected_moods = valid_closed[:3]
    incomplete = [m for m in moods if not (m.closed and m.date)]
    selected_interactions = list(interactions)
    selected_feedback = feedback[:10]
    all_refs = [x.source_ref for x in [*moods, *interactions, *feedback]]
    warnings: list[str] = []
    truncated = len(valid_closed) > 3 or len(feedback) > 10
    if truncated:
        warnings.append("Контекст сокращён детерминированно по дате и типу.")
    if incomplete:
        warnings.append("Незавершённые MM показаны отдельно и не участвуют в динамике.")
    # Keep all fetched Jira comments. Only the additional feedback uses this budget.
    text_chars = sum(len(item.text) for item in selected_interactions + selected_feedback)
    while selected_feedback and text_chars > max_context_chars:
        text_chars -= len(selected_feedback.pop().text)
        truncated = True
    included = [x.source_ref for x in [*selected_moods, *selected_interactions, *selected_feedback]]
    return (
        selected_moods,
        incomplete,
        selected_interactions,
        selected_feedback,
        ContextSelection(
            included_source_refs=included,
            excluded_source_refs=sorted(set(all_refs) - set(included)),
            warnings=warnings,
            truncated=truncated,
        ),
    )


def normalize_bundle(
    *,
    employee_issue: dict[str, Any],
    comments: list[dict[str, Any]],
    children: list[dict[str, Any]],
    mapping: FieldMapping,
    hmac_key: bytes,
    current_time: datetime,
    max_comment_chars: int = 12_000,
    max_context_chars: int = 80_000,
    include_display_name: bool = False,
    mood_issue_type_names: list[str] | None = None,
    feedback_issue_type_names: list[str] | None = None,
) -> NormalizedContext:
    key = str(employee_issue.get("key", "UNKNOWN-1")).upper()
    fields = employee_issue.get("fields") if isinstance(employee_issue.get("fields"), dict) else {}
    interactions = normalize_interactions(comments, max_comment_chars)
    moods, feedback = normalize_children(
        children,
        max_comment_chars,
        mapping,
        mood_issue_type_names,
        feedback_issue_type_names,
    )
    selected_moods, incomplete, selected_interactions, selected_feedback, selection = (
        select_context(moods, interactions, feedback, max_context_chars)
    )
    employee = Employee(
        employee_ref=employee_reference(key, hmac_key),
        display_name=_plain_value(fields.get("summary")) if include_display_name else None,
        jira_key=key,
        issue_type=_plain_value(fields.get("issuetype")) or "Employee",
        status=_status_name(fields),
        role=_plain_value(fields.get(mapping.role or "")),
        seniority=_plain_value(fields.get(mapping.seniority or "")),
        department=_plain_value(fields.get(mapping.department or "")),
        attestation_date=parse_date(fields.get(mapping.attestation_date or "")),
        last_meaningful_interaction_at=interactions[0].date if interactions else None,
        jira_recorded_risk=JiraRecordedRisk(
            level=_plain_value(fields.get(mapping.risk or "")),
            type=_plain_value(fields.get(mapping.risk_type or "")),
        ),
        jira_recorded_value=JiraRecordedValue(
            level=_plain_value(fields.get(mapping.value or "")),
            types=_multi_value(fields.get(mapping.value_type or "")),
        ),
    )
    return NormalizedContext(
        generated_at=current_time,
        current_date=current_time.date(),
        employee=employee,
        mood_monitoring=selected_moods,
        incomplete_mood_monitoring=incomplete,
        mood_dynamics=calculate_mm_dynamics(selected_moods),
        interactions=selected_interactions,
        feedback=selected_feedback,
        context_selection=selection,
    )


def apply_exclusions(context: NormalizedContext, excluded: set[str]) -> NormalizedContext:
    copy = context.model_copy(deep=True)
    copy.mood_monitoring = [x for x in copy.mood_monitoring if x.source_ref not in excluded]
    copy.incomplete_mood_monitoring = [
        x for x in copy.incomplete_mood_monitoring if x.source_ref not in excluded
    ]
    copy.interactions = [x for x in copy.interactions if x.source_ref not in excluded]
    copy.feedback = [x for x in copy.feedback if x.source_ref not in excluded]
    retained = {
        x.source_ref
        for x in [
            *copy.mood_monitoring,
            *copy.incomplete_mood_monitoring,
            *copy.interactions,
            *copy.feedback,
        ]
    }
    copy.mood_dynamics = [
        d
        for d in copy.mood_dynamics
        if d.from_source_ref in retained and d.to_source_ref in retained
    ]
    # Do not disclose even identifiers for records that are outside the selected model context.
    # The UI keeps the full preview separately in session memory.
    copy.context_selection.excluded_source_refs = []
    copy.context_selection.included_source_refs = sorted(retained)
    return copy
