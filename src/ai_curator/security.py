from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from pathlib import Path
from urllib.parse import urlparse

from .errors import CuratorError


def read_secret_file(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return value or None


def get_or_create_session_hmac_key(file_path: Path | None = None) -> bytes:
    if file_path:
        value = read_secret_file(file_path)
        if value:
            return value.encode("utf-8")
    return secrets.token_bytes(32)


def employee_reference(jira_key: str, key: bytes) -> str:
    digest = hmac.new(key, jira_key.upper().encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"emp_{digest}"


def validate_jira_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "mock-jira"}:
        raise CuratorError(
            "JIRA_URL_TLS_REQUIRED",
            "Для Jira нужен HTTPS. HTTP разрешён только для локального mock-сервера.",
        )
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CuratorError(
            "JIRA_URL_INVALID", "Проверьте базовый URL Jira: без логина, query и fragment."
        )
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if not (ip.is_private or ip.is_loopback):
            raise CuratorError(
                "JIRA_URL_PUBLIC_IP", "Разрешён только корпоративный или локальный адрес Jira."
            )
    except ValueError:
        pass
    return value.rstrip("/")
