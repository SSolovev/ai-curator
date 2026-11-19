from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .domain import FieldMapping, NormalizedContext
from .jira import JiraTransportConfig
from .security import get_or_create_session_hmac_key

GenerationStatus = Literal["running", "succeeded", "failed", "cancelled"]


@dataclass
class GenerationJob:
    generation_id: str
    status: GenerationStatus = "running"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    error_status_code: int = 500
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    jira_issue_key: str | None = None
    jira_connection: object | None = field(default=None, repr=False)
    published_comments: dict[str, str] = field(default_factory=dict, repr=False)
    publication_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def mark_cancelled(self) -> None:
        if self.status == "cancelled":
            return
        self.status = "cancelled"
        self.error = {"code": "GENERATION_CANCELLED", "message": "Генерация отменена."}
        self.error_status_code = 409
        self.finished_at = time.monotonic()

    def status_payload(self) -> dict[str, Any]:
        finished_at = self.finished_at or time.monotonic()
        payload: dict[str, Any] = {
            "generation_id": self.generation_id,
            "status": self.status,
            "elapsed_seconds": round(finished_at - self.started_at, 1),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass
class SessionState:
    jira_config: JiraTransportConfig | None = None
    jira_connection: object = field(default_factory=object, repr=False)
    mapping: FieldMapping = field(default_factory=FieldMapping)
    context: NormalizedContext | None = None
    hmac_key: bytes = field(default_factory=get_or_create_session_hmac_key)
    cancel_events: dict[str, asyncio.Event] = field(default_factory=dict)
    generation_jobs: dict[str, GenerationJob] = field(default_factory=dict)

    def clear(self) -> None:
        for event in self.cancel_events.values():
            event.set()
        for generation_id, job in self.generation_jobs.items():
            if (
                generation_id not in self.cancel_events
                and job.task is not None
                and not job.task.done()
            ):
                job.task.cancel()
        self.jira_config = None
        self.jira_connection = object()
        self.context = None
        self.cancel_events.clear()
        self.generation_jobs.clear()


class SessionStore:
    def __init__(self, hmac_key: bytes | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._hmac_key = hmac_key

    def get(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(
                hmac_key=self._hmac_key or get_or_create_session_hmac_key(),
            )
        return self._sessions[session_id]

    def delete(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state:
            state.clear()
