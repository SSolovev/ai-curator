from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import CuratorError

AGENDA_SCHEDULES = {
    1: ((0, 30),),
    2: ((0, 10), (10, 30)),
    3: ((0, 5), (5, 25), (25, 30)),
    4: ((0, 5), (5, 15), (15, 25), (25, 30)),
    5: ((0, 3), (3, 10), (10, 20), (20, 26), (26, 30)),
}


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def all_source_refs(context: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for section in ("mood_monitoring", "incomplete_mood_monitoring", "interactions", "feedback"):
        for item in context.get(section, []):
            if isinstance(item, dict) and isinstance(item.get("source_ref"), str):
                refs.add(item["source_ref"])
    return refs


def source_dates(context: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for section in ("mood_monitoring", "incomplete_mood_monitoring", "interactions", "feedback"):
        for item in context.get(section, []):
            if isinstance(item, dict) and item.get("source_ref") and item.get("date"):
                result[item["source_ref"]] = str(item["date"])[:10]
    return result


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def finalize_computed_fields(result: Any, context: dict[str, Any]) -> Any:
    """Add deterministic fields that the model should not calculate."""
    if not isinstance(result, dict):
        return result

    finalized = deepcopy(result)
    valid_refs = all_source_refs(context)
    valid_dates = source_dates(context)
    used_refs: set[str] = set()

    agenda = finalized.get("agenda")
    if isinstance(agenda, list):
        schedule = AGENDA_SCHEDULES.get(len(agenda))
        if schedule is not None:
            for slot, (start_minute, end_minute) in zip(agenda, schedule, strict=True):
                if isinstance(slot, dict):
                    slot["start_minute"] = start_minute
                    slot["end_minute"] = end_minute

    # A date is source metadata, so always take it from the canonical context.
    for node in _walk(finalized):
        evidence = node.get("evidence")
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict):
                continue
            source_ref = item.get("source_ref")
            if not isinstance(source_ref, str):
                continue
            used_refs.add(source_ref)
            canonical_date = valid_dates.get(source_ref)
            if canonical_date is not None:
                item["date"] = canonical_date

    selection = context.get("context_selection")
    if not isinstance(selection, dict):
        selection = {}

    warnings: list[str] = []
    raw_warnings = selection.get("warnings", [])
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            if not isinstance(warning, str):
                continue
            normalized_warning = " ".join(warning.split())[:300]
            if normalized_warning and normalized_warning not in warnings:
                warnings.append(normalized_warning)

    unused_refs = sorted(valid_refs - used_refs)
    if len(unused_refs) > 50:
        unused_refs = unused_refs[:50]
        warnings.append("Список неиспользованных источников сокращён до 50 записей.")
    if not valid_refs:
        warnings.append("В выбранном контексте нет источников, пригодных для цитирования.")

    finalized["data_quality"] = {
        "warnings": warnings[:20],
        "context_cut": bool(selection.get("truncated", False)),
        "unused_source_refs": unused_refs,
    }
    return finalized


def validate_result(result: Any, context: dict[str, Any], _schema: dict[str, Any]) -> dict[str, Any]:
    """Validate only the minimal integrity guarantees retained by the application.

    The response schema is still supplied to the model as an output-format hint,
    but the backend intentionally does not reject responses for schema, size,
    schedule, date-format, wording, or policy constraints.
    """
    if not isinstance(result, dict):
        raise CuratorError("LLM_SCHEMA_INVALID", "Модель вернула ответ неизвестного формата.", 502)

    valid_refs = all_source_refs(context)
    for node in _walk(result):
        evidence = node.get("evidence")
        if node.get("certainty") == "fact" and (
            not isinstance(evidence, list) or not evidence
        ):
            raise CuratorError(
                "LLM_EVIDENCE_REQUIRED",
                "Фактический вывод модели не содержит источника.",
                502,
            )
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict) or item.get("source_ref") not in valid_refs:
                raise CuratorError(
                    "LLM_SOURCE_UNKNOWN",
                    "Модель сослалась на отсутствующий источник.",
                    502,
                )

    unused = result["data_quality"]["unused_source_refs"]
    if any(ref not in valid_refs for ref in unused):
        raise CuratorError("LLM_SOURCE_UNKNOWN", "Модель сослалась на отсутствующий источник.", 502)
    return result
