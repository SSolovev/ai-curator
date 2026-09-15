from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret configuration. Secrets are read separately from mounted files."""

    model_config = SettingsConfigDict(env_prefix="CURATOR_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8080
    production: bool = True
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    llm_runtime: Literal["llama_cpp", "ollama", "external"] = "llama_cpp"
    llm_base_url: str = "http://model:8080"
    llm_model: str = "local-model"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "ai-curator-gemma4-12b"
    external_llm_enabled: bool = False
    external_llm_base_url: str = "https://api.openai.com/v1"
    external_llm_model: str = "gpt-4.1-mini"
    external_llm_api_key_file: Path = Path("/secrets/external_llm_api_key")
    pii_filter_base_url: str = "http://host.docker.internal:11434"
    pii_filter_model: str = "ai-curator-pii-gemma3-4b"
    pii_filter_dictionary_file: Path = Path("/secrets/pii_dictionary.txt")
    pii_filter_timeout_seconds: float = Field(default=300.0, ge=10.0, le=1800.0)
    pii_filter_batch_items: int = Field(default=20, ge=1, le=50)
    pii_filter_batch_chars: int = Field(default=12_000, ge=1000, le=40_000)
    llm_timeout_seconds: float = 240.0
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=6000, ge=1000, le=16_000)
    llm_reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    llm_comment_batch_size: int = Field(default=10, ge=1, le=10)
    llm_comment_batch_chars: int = Field(default=24_000, ge=4000, le=80_000)
    llm_repair_enabled: bool = False
    llm_log_request_response: bool = False
    jira_connect_timeout_seconds: float = 8.0
    jira_read_timeout_seconds: float = 25.0
    jira_log_request_response: bool = False
    jira_log_invalid_response_body: bool = False
    jira_log_field_catalog: bool = False
    jira_log_employee_issue: bool = False
    jira_log_response_max_chars: int = Field(default=4000, ge=256, le=20_000)
    jira_page_size: int = Field(default=50, ge=1, le=100)
    jira_max_pages: int = Field(default=100, ge=1, le=500)
    jira_max_comments: int = Field(default=50, ge=1, le=50)
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

    @field_validator("llm_base_url", "ollama_base_url", "pii_filter_base_url")
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
            raise ValueError("model runtime URL must point to an allowed local host")
        return value.rstrip("/")

    @field_validator("external_llm_base_url")
    @classmethod
    def secure_external_llm_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("external model URL must use HTTPS and contain no credentials")
        return value.rstrip("/")

    @field_validator("external_llm_model", "pii_filter_model")
    @classmethod
    def nonempty_model_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model name must not be empty")
        return value

    @model_validator(mode="after")
    def external_runtime_requires_explicit_opt_in(self) -> Settings:
        if self.llm_runtime == "external" and not self.external_llm_enabled:
            raise ValueError(
                "external runtime requires CURATOR_EXTERNAL_LLM_ENABLED=true"
            )
        if self.llm_runtime == "external":
            for path in (
                self.external_llm_api_key_file,
                self.pii_filter_dictionary_file,
            ):
                normalized_path = str(path).replace("\\", "/")
                if (
                    not normalized_path.startswith("/secrets/")
                    or "/../" in normalized_path
                    or normalized_path.endswith("/..")
                ):
                    raise ValueError(
                        "external runtime secret files must be inside /secrets"
                    )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
