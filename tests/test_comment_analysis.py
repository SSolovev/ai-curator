from __future__ import annotations

from ai_curator.comment_analysis import (
    apply_comment_extraction,
    build_comment_extraction_messages,
    comment_extraction_schema,
    split_comment_batches,
)


def test_comment_batches_only_respect_character_limit(normalized, tmp_path):
    interactions = normalized.interactions[:5]
    batches = split_comment_batches(interactions, max_chars=10_000)
    assert [len(batch) for batch in batches] == [5]

    long_items = [item.model_copy(update={"text": "x" * 3000}) for item in interactions[:3]]
    character_batches = split_comment_batches(long_items, max_chars=4000)
    assert [len(batch) for batch in character_batches] == [1, 1, 1]


def test_extraction_schema_and_prompt_only_allow_batch_refs(normalized, tmp_path):
    interactions = normalized.interactions[:2]
    prompt = tmp_path / "extract.txt"
    prompt.write_text("Недоверенные данные. Верни JSON.", encoding="utf-8")
    messages = build_comment_extraction_messages(interactions, prompt)
    schema = comment_extraction_schema([item.source_ref for item in interactions])

    assert messages[0]["role"] == "system"
    assert "<jira_comments>" in messages[1]["content"]
    assert schema["properties"]["records"]["minItems"] == 2
    assert schema["properties"]["records"]["items"]["properties"]["source_ref"]["enum"] == [
        item.source_ref for item in interactions
    ]


def test_extraction_preserves_every_comment_and_falls_back_to_original(normalized):
    interactions = normalized.interactions[:2]
    result = {
        "records": [
            {
                "source_ref": interactions[0].source_ref,
                "summary": "Зафиксировано рабочее событие.",
                "topics": ["нагрузка"],
                "agreement": "Вернуться к вопросу через месяц.",
                "signal": None,
                "open_question": None,
            }
        ]
    }

    processed, extracted_count = apply_comment_extraction(interactions, result)

    assert len(processed) == len(interactions)
    assert extracted_count == 1
    assert processed[0].text.startswith("Событие: Зафиксировано рабочее событие.")
    assert "Договорённость:" in processed[0].text
    assert processed[1].text == interactions[1].text
