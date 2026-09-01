from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret configuration. Secrets are read separately from mounted files."""

    model_config = SettingsConfigDict(env_prefix="CURATOR_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8080
    production: bool = True
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    llm_base_url: str = "http://model:8080"
    llm_model: str = "local-model"
    llm_timeout_seconds: float = 240.0
    llm_repair_enabled: bool = False
    jira_connect_timeout_seconds: float = 8.0
    jira_read_timeout_seconds: float = 25.0
    jira_log_invalid_response_body: bool = False
    jira_log_field_catalog: bool = False
    jira_log_employee_issue: bool = False
    jira_log_response_max_chars: int = Field(default=4000, ge=256, le=20_000)
    jira_page_size: int = Field(default=50, ge=1, le=100)
    jira_max_pages: int = Field(default=100, ge=1, le=500)
    max_comment_chars: int = Field(default=12_000, ge=1000, le=100_000)
    max_context_chars: int = Field(default=80_000, ge=10_000, le=500_000)
    max_jira_response_bytes: int = Field(default=10_000_000, ge=100_000, le=50_000_000)
    token_file: Path = Path("/run/secrets/jira_token")
    hmac_key_file: Path = Path("/run/secrets/hmac_key")
    data_dir: Path = Path("./data")
    mood_issue_type_names: list[str] = ["Mood Monitoring"]
    feedback_issue_type_names: list[str] = ["Feedback", "KPI"]

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        # 0.0.0.0 is only accepted for the hardened container; host port remains loopback-bound.
        if value not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:  # noqa: S104
            raise ValueError("host must be loopback, or 0.0.0.0 only inside a protected container")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def local_llm_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        allowed_hosts = {"model", "localhost", "127.0.0.1", "::1", "host.docker.internal"}
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("llm_base_url must point to the local model runtime")
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
