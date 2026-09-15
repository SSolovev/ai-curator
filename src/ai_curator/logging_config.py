from __future__ import annotations

import contextvars
import json
import logging
import sys

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only an allowlist is emitted. Full LLM/Jira bodies reach it only through
        # explicit diagnostic flags; never pass source data as record arguments.
        payload = {
            "level": record.levelname,
            "event": record.msg if isinstance(record.msg, str) else "application_event",
            "correlation_id": correlation_id_var.get(),
            "code": getattr(record, "code", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        for field_name in (
            "operation",
            "runtime",
            "record_count",
            "redaction_count",
            "attempt",
            "http_method",
            "status_code",
            "content_type",
            "request_path",
            "request_url",
            "request_params",
            "request_body",
            "redirect_location",
            "response_body",
            "response_truncated",
            "finish_reason",
            "prompt_tokens",
            "completion_tokens",
        ):
            if hasattr(record, field_name):
                payload[field_name] = getattr(record, field_name)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Third-party HTTP logs may include Jira paths. Emit only our allowlisted events.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
