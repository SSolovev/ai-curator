from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .domain import Interaction


def comment_extraction_schema(source_refs: list[str]) -> dict[str, Any]:
    """Build a strict per-batch schema that only permits known Jira source refs."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["records"],
        "properties": {
            "records": {
                "type": "array",
                "minItems": len(source_refs),
                "maxItems": len(source_refs),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "source_ref",
                        "summary",
                        "topics",
                        "agreement",
                        "signal",
                        "open_question",
                    ],
                    "properties": {
                        "source_ref": {"enum": source_refs},
                        "summary": {"type": "string", "minLength": 1, "maxLength": 400},
                        "topics": {
                            "type": "array",
                            "maxItems": 3,
                            "items": {"type": "string", "minLength": 1, "maxLength": 100},
                        },
                        "agreement": {"type": ["string", "null"], "maxLength": 250},
                        "signal": {"type": ["string", "null"], "maxLength": 250},
                        "open_question": {"type": ["string", "null"], "maxLength": 250},
                    },
                },
            }
        },
    }


def build_comment_extraction_messages(
    interactions: list[Interaction], system_path: Path
) -> list[dict[str, str]]:
    system = system_path.read_text(encoding="utf-8").strip()
    payload = [
        {
            "source_ref": item.source_ref,
            "date": item.date.isoformat(),
            "type": item.type,
            "text": item.text,
        }
        for item in interactions
    ]
    user = (
        "Извлеки рабочие события из каждой записи. Верни ровно одну запись результата "
        "для каждого source_ref.\n\n<jira_comments>\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        "\n</jira_comments>"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def split_comment_batches(
    interactions: list[Interaction], *, max_items: int, max_chars: int
) -> list[list[Interaction]]:
    batches: list[list[Interaction]] = []
    current: list[Interaction] = []
    current_chars = 0
    for interaction in interactions:
        text_chars = len(interaction.text)
        if current and (len(current) >= max_items or current_chars + text_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(interaction)
        current_chars += text_chars
    if current:
        batches.append(current)
    return batches


def apply_comment_extraction(
    interactions: list[Interaction], result: dict[str, Any]
) -> tuple[list[Interaction], int]:
    raw_records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(raw_records, list):
        return [item.model_copy(deep=True) for item in interactions], 0

    allowed_refs = {item.source_ref for item in interactions}
    extracted: dict[str, dict[str, Any]] = {}
    for record in raw_records:
        if not isinstance(record, dict):
            continue
        source_ref = record.get("source_ref")
        summary = _clean_string(record.get("summary"))
        if source_ref not in allowed_refs or source_ref in extracted or not summary:
            continue
        extracted[source_ref] = record

    output: list[Interaction] = []
    for interaction in interactions:
        copy = interaction.model_copy(deep=True)
        record = extracted.get(interaction.source_ref)
        if record is not None:
            copy.text = _render_record(record)
        output.append(copy)
    return output, len(extracted)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _render_record(record: dict[str, Any]) -> str:
    lines = [f"Событие: {_clean_string(record.get('summary'))}"]
    topics = record.get("topics")
    if isinstance(topics, list):
        cleaned_topics = [value for item in topics if (value := _clean_string(item))]
        if cleaned_topics:
            lines.append(f"Темы: {', '.join(cleaned_topics)}")
    for key, label in (
        ("agreement", "Договорённость"),
        ("signal", "Сигнал"),
        ("open_question", "Открытый вопрос"),
    ):
        if value := _clean_string(record.get(key)):
            lines.append(f"{label}: {value}")
    return "\n".join(lines)
