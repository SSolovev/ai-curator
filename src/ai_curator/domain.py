from __future__ import annotations

from datetime import date as Date
from datetime import datetime as DateTime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ModelRuntime = Literal["llama_cpp", "ollama", "external"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JiraRecordedRisk(StrictModel):
    level: str | None = None
    type: str | None = None


class JiraRecordedValue(StrictModel):
    level: str | None = None
    types: list[str] = Field(default_factory=list)


class Employee(StrictModel):
    employee_ref: str
    display_name: str | None = None
    jira_key: str
    issue_type: str = "Employee"
    status: str | None = None
    role: str | None = None
    seniority: str | None = None
    department: str | None = None
    attestation_date: Date | None = None
    last_meaningful_interaction_at: DateTime | None = None
    jira_recorded_risk: JiraRecordedRisk = Field(default_factory=JiraRecordedRisk)
    jira_recorded_value: JiraRecordedValue = Field(default_factory=JiraRecordedValue)


class MoodScores(StrictModel):
    work_life_balance: float | None = None
    work_content: float | None = None
    client_processes: float | None = None
    equipment: float | None = None
    recommendation: float | None = None
    manager_interaction: float | None = None
    help_contact: float | None = None
    it1_helps: float | None = None


class MoodMonitoring(StrictModel):
    source_ref: str
    period: str | None = None
    date: Date | None = None
    status: str | None = None
    scores: MoodScores = Field(default_factory=MoodScores)
    comment: str | None = None
    closed: bool = False


class Interaction(StrictModel):
    source_ref: str
    date: DateTime
    type: str | None = None
    original_type: str | None = None
    author_role: str = "unknown"
    text: str
    visibility: str | None = None
    relevance: int = 0


class Feedback(StrictModel):
    source_ref: str
    date: Date | None = None
    type: str | None = None
    text: str
    sentiment_source: str | None = None


class MMChange(StrictModel):
    metric: str
    from_value: float
    to_value: float
    delta: float
    from_source_ref: str
    to_source_ref: str


class ContextSelection(StrictModel):
    policy: str = "latest_3_closed_mm_up_to_50_comments_and_latest_10_feedback"
    included_source_refs: list[str] = Field(default_factory=list)
    excluded_source_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


class NormalizedContext(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: DateTime
    current_date: Date
    employee: Employee
    mood_monitoring: list[MoodMonitoring] = Field(default_factory=list)
    incomplete_mood_monitoring: list[MoodMonitoring] = Field(default_factory=list)
    mood_dynamics: list[MMChange] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    feedback: list[Feedback] = Field(default_factory=list)
    context_selection: ContextSelection = Field(default_factory=ContextSelection)


class FieldMapping(StrictModel):
    role: str | None = "customfield_11411"
    seniority: str | None = "customfield_11412"
    department: str | None = "customfield_14101"
    attestation_date: str | None = "customfield_11459"
    risk: str | None = "customfield_11435"
    risk_type: str | None = "customfield_11901"
    value: str | None = "customfield_11434"
    value_type: str | None = "customfield_11900"
    feedback_comments: str | None = "customfield_14804"


class ConnectionInput(StrictModel):
    jira_base_url: str
    token: str = ""
    ca_bundle_path: str | None = None
    proxy_url: str | None = None


class GenerateInput(StrictModel):
    excluded_source_refs: list[str] = Field(default_factory=list, max_length=500)
    # Accepted for compatibility with older callers. Generation always hides the name.
    include_display_name: bool = False


class ExportInput(StrictModel):
    format: Literal["json", "markdown"]
    result: dict


class PublishCommentInput(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
