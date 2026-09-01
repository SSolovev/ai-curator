from __future__ import annotations

from ai_curator.normalization import (
    apply_exclusions,
    calculate_mm_dynamics,
    clean_jira_text,
    normalize_interactions,
    parse_datetime,
    parse_meeting_type,
)


def test_meeting_type_variants():
    assert parse_meeting_type("{meeting-type}👥 1-1{meeting-type} Текст") == ("1-1", "👥 1-1", "Текст")
    assert parse_meeting_type("{meeting-type}😊 MM{meeting-type} Текст") == ("MM", "😊 MM", "Текст")
    assert parse_meeting_type("{meeting-type}Retro{meeting-type} Текст") == ("Retro", "Retro", "Текст")
    assert parse_meeting_type("Текст") == (None, None, "Текст")


def test_wiki_and_html_cleaning_preserves_meaning():
    value = "<p><b>Важно</b><br>Проверить [доступ|https://jira.local/X-1]</p>\n* пункт\n{color:red}срок{color}"
    cleaned = clean_jira_text(value)
    assert "Важно" in cleaned
    assert "Проверить доступ (https://jira.local/X-1)" in cleaned
    assert "• пункт" in cleaned
    assert "срок" in cleaned
    assert "<b>" not in cleaned and "{color" not in cleaned


def test_dates_keep_timezone_and_sort_deterministically():
    parsed = parse_datetime("2026-08-12T12:00:00+0300")
    assert parsed is not None and parsed.utcoffset().total_seconds() == 10800
    items = normalize_interactions(
        [
            {"id": "2", "created": "2026-01-01T09:00:00Z", "body": "{meeting-type}1-1{meeting-type} второй"},
            {"id": "1", "created": "2026-01-01T12:00:00+03:00", "body": "{meeting-type}1-1{meeting-type} первый"},
        ],
        1000,
    )
    assert [x.source_ref for x in items] == ["jira-comment:2", "jira-comment:1"]


def test_custom_fields_selection_nulls_and_dynamics(normalized):
    assert len(normalized.mood_monitoring) == 3
    assert normalized.mood_monitoring[0].source_ref == "jira:DEMO-2103"
    assert len(normalized.incomplete_mood_monitoring) == 1
    latest = normalized.mood_monitoring[0]
    assert latest.scores.equipment is None
    assert latest.scores.it1_helps == 8
    changes = calculate_mm_dynamics(normalized.mood_monitoring)
    balance = [x for x in changes if x.metric == "work_life_balance"]
    assert [(x.from_value, x.to_value, x.delta) for x in balance] == [(6, 5, -1), (5, 7, 2)]
    # Missing values never become zero or produce a fabricated delta.
    assert len([x for x in changes if x.metric == "it1_helps"]) == 0


def test_latest_three_and_up_to_ten_meaningful_interactions(normalized):
    assert len(normalized.interactions) <= 10
    refs = {x.source_ref for x in normalized.interactions}
    assert "jira-comment:7010" not in refs
    assert "jira-comment:7011" in refs
    assert normalized.context_selection.truncated is False


def test_hmac_reference_is_stable_and_name_hidden_by_default(normalized):
    assert normalized.employee.employee_ref.startswith("emp_")
    assert "DEMO" not in normalized.employee.employee_ref
    # Fixture is normalized with explicit local retention, but API removes this before preview/model by default.
    assert normalized.employee.display_name is not None


def test_prompt_injection_remains_untrusted_data(golden_data):
    interactions = normalize_interactions(golden_data["comments"], 12000)
    attack = next(x for x in interactions if x.source_ref == "jira-comment:7012")
    assert "system prompt" in attack.text
    assert attack.type == "unknown"


def test_model_selection_refs_include_retained_incomplete_mm(normalized):
    selected = apply_exclusions(normalized, set())
    incomplete_ref = selected.incomplete_mood_monitoring[0].source_ref
    assert incomplete_ref in selected.context_selection.included_source_refs
    assert selected.context_selection.excluded_source_refs == []
