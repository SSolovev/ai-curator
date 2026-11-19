from __future__ import annotations

from typing import Any


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
        return (
            ", ".join(f"{safe(item['date'])} — `{safe(item['source_ref'])}`" for item in items)
            or "не указан"
        )

    history = result["history_summary"]
    lines = [
        "# Сценарий встречи 1:1",
        "",
        "## 1. Краткий анализ истории",
        "",
        safe(history["overall_dynamics"]),
    ]
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
    lines.extend(
        [
            "",
            "## 5. Скрипт 30-минутной встречи",
            "",
            "| Время | Цель этапа | Реплики куратора | Основные вопросы | Сигналы |",
            "|---|---|---|---|---|",
        ]
    )
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
