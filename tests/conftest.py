from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_curator.domain import FieldMapping
from ai_curator.normalization import normalize_bundle
from ai_curator.privacy_filter import PrivacyFilterSession

FIXTURES = Path(__file__).parent / "fixtures"


class NoopPrivacyFilter:
    """Test double for cases that exercise behavior outside privacy filtering."""

    async def health(self):
        return {"ok": True}

    async def sanitize_context(self, context, cancel_event):
        sanitized = context.model_copy(deep=True)
        sanitized.employee.display_name = None
        return PrivacyFilterSession(sanitized, {})

    @staticmethod
    def restore_source_refs(value, session):
        return value


@pytest.fixture
def noop_privacy_filter():
    return NoopPrivacyFilter()


@pytest.fixture
def golden_data():
    return json.loads((FIXTURES / "golden_jira.json").read_text(encoding="utf-8"))


@pytest.fixture
def mapping():
    return FieldMapping(
        role="customfield_11411",
        seniority="customfield_11412",
        department="customfield_14101",
        attestation_date="customfield_11459",
        risk="customfield_11435",
        risk_type="customfield_11901",
        value="customfield_11434",
        value_type="customfield_11900",
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
