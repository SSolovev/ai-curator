from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_curator.domain import FieldMapping
from ai_curator.normalization import normalize_bundle

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def golden_data():
    return json.loads((FIXTURES / "golden_jira.json").read_text(encoding="utf-8"))


@pytest.fixture
def mapping():
    return FieldMapping(
        role="customfield_20001",
        seniority="customfield_20002",
        department="customfield_20003",
        attestation_date="customfield_20004",
        risk="customfield_20005",
        risk_type="customfield_20006",
        feedback_comments="customfield_14804",
    )


@pytest.fixture
def normalized(golden_data, mapping):
    return normalize_bundle(
        employee_issue=golden_data["employee"],
        comments=golden_data["comments"],
        children=golden_data["children"],
        mapping=mapping,
        hmac_key=b"test-key-that-never-leaves-the-session",
        current_time=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        include_display_name=True,
    )


@pytest.fixture
def valid_result():
    return json.loads((FIXTURES / "valid_llm_response.json").read_text(encoding="utf-8"))
