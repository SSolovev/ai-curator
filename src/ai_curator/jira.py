from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .errors import CuratorError, jira_http_error
from .security import validate_jira_url

EMPLOYEE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,19}-[1-9][0-9]{0,9}$")
JSON_SECRET_RE = re.compile(
    r'(?i)(["\'](?:access[_-]?token|refresh[_-]?token|csrf[_-]?token|xsrf[_-]?token|password|passwd)["\']\s*:\s*["\'])([^"\']+)'
)
PARAM_SECRET_RE = re.compile(
    r"(?i)((?:access[_-]?token|refresh[_-]?token|csrf[_-]?token|xsrf[_-]?token|password|passwd)=)([^&\s<>]+)"
)
HTML_SECRET_RE = re.compile(
    r'(?i)(name=["\'](?:access[_-]?token|refresh[_-]?token|csrf[_-]?token|xsrf[_-]?token|password|passwd)["\'][^>]*value=["\'])([^"\']+)'
)
logger = logging.getLogger("ai_curator.jira")


def _safe_redirect_location(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _redact_response_text(text: str, token: str) -> str:
    if token:
        text = text.replace(token, "[REDACTED]")
    text = JSON_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = PARAM_SECRET_RE.sub(r"\1[REDACTED]", text)
    return HTML_SECRET_RE.sub(r"\1[REDACTED]", text)


def _response_preview(body: bytes, encoding: str | None, token: str, max_chars: int) -> tuple[str, bool]:
    try:
        text = body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    text = _redact_response_text(text, token)
    truncated = len(text) > max_chars
    return text[:max_chars], truncated


def validate_employee_key(value: str) -> str:
    normalized = value.strip().upper()
    if not EMPLOYEE_KEY_RE.fullmatch(normalized):
        raise CuratorError(
            "EMPLOYEE_KEY_INVALID",
            "Ключ должен выглядеть как PROJECT-123 и содержать только латинские буквы и цифры.",
        )
    return normalized


@dataclass(frozen=True)
class JiraTransportConfig:
    base_url: str
    token: str
    ca_bundle_path: str | None = None
    proxy_url: str | None = None
    connect_timeout: float = 8.0
    read_timeout: float = 25.0
    log_invalid_response_body: bool = False
    log_field_catalog: bool = False
    log_employee_issue: bool = False
    log_response_max_chars: int = 4000
    max_response_bytes: int = 10_000_000
    max_retries: int = 2


class JiraClient:
    def __init__(self, config: JiraTransportConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        base_url = validate_jira_url(config.base_url)
        verify: bool | str = validate_ca_path(config.ca_bundle_path) or True
        timeout = httpx.Timeout(config.read_timeout, connect=config.connect_timeout)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {config.token}", "Accept": "application/json"},
            verify=verify,
            proxy=config.proxy_url,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self) -> "JiraClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                async with self._client.stream("GET", path, params=params) as response:
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < self.config.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = min(float(retry_after), 5.0) if retry_after and retry_after.isdigit() else 0.25 * 2**attempt
                        await response.aread()
                        # Jitter is scheduling noise, not a security token.
                        await asyncio.sleep(delay + random.uniform(0, 0.05))  # noqa: S311
                        continue
                    if response.status_code >= 400:
                        await response.aread()
                        raise jira_http_error(response.status_code)
                    announced_length = response.headers.get("Content-Length")
                    if (
                        announced_length
                        and announced_length.isdigit()
                        and int(announced_length) > self.config.max_response_bytes
                    ):
                        raise CuratorError(
                            "JIRA_RESPONSE_TOO_LARGE",
                            "Ответ Jira слишком большой и не был обработан.",
                            502,
                        )
                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.config.max_response_bytes:
                            raise CuratorError(
                                "JIRA_RESPONSE_TOO_LARGE",
                                "Ответ Jira слишком большой и не был обработан.",
                                502,
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if not body:
                        raise CuratorError("JIRA_EMPTY_RESPONSE", "Jira вернула пустой ответ.", 502)
                    try:
                        return json.loads(body)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        response_body = None
                        response_truncated = False
                        if self.config.log_invalid_response_body:
                            response_body, response_truncated = _response_preview(
                                body,
                                response.encoding,
                                self.config.token,
                                self.config.log_response_max_chars,
                            )
                        logger.warning(
                            "jira_invalid_json_response",
                            extra={
                                "status_code": response.status_code,
                                "content_type": response.headers.get("Content-Type"),
                                "request_path": response.request.url.path,
                                "redirect_location": _safe_redirect_location(response.headers.get("Location")),
                                "response_body": response_body,
                                "response_truncated": response_truncated,
                            },
                        )
                        raise CuratorError("JIRA_INVALID_JSON", "Jira вернула ответ неизвестного формата.", 502) from exc
            except CuratorError:
                raise
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    await asyncio.sleep(0.25 * 2**attempt)
                    continue
                raise CuratorError("JIRA_TIMEOUT", "Jira не ответила вовремя. Проверьте VPN и повторите.", 504) from exc
            except httpx.TransportError as exc:
                name = type(exc).__name__.lower()
                if "ssl" in name or "cert" in str(exc).lower():
                    raise CuratorError("JIRA_TLS", "Не удалось проверить TLS-сертификат Jira. Проверьте корпоративный CA bundle.", 502) from exc
                raise CuratorError("JIRA_NETWORK", "Нет соединения с Jira. Проверьте VPN, proxy и адрес.", 502) from exc
        raise CuratorError("JIRA_NETWORK", "Нет соединения с Jira.", 502) from last_error

    async def myself(self) -> dict[str, Any]:
        data = await self._request("/rest/api/2/myself")
        if not isinstance(data, dict):
            raise CuratorError("JIRA_INVALID_JSON", "Jira вернула неожиданный формат профиля.", 502)
        return data

    async def fields(self) -> list[dict[str, Any]]:
        data = await self._request("/rest/api/2/field")
        if self.config.log_field_catalog:
            logger.info(
                "jira_field_catalog_response",
                extra={
                    "status_code": 200,
                    "content_type": "application/json",
                    "request_path": "/rest/api/2/field",
                    "response_body": _redact_response_text(
                        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                        self.config.token,
                    ),
                    "response_truncated": False,
                },
            )
        return data if isinstance(data, list) else []

    async def search_pages(
        self, *, jql: str, fields: list[str], page_size: int = 50, max_pages: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        start_at = 0
        for _ in range(max_pages):
            data = await self._request(
                "/rest/api/2/search",
                params={
                    "jql": jql,
                    "fields": ",".join(fields),
                    "startAt": start_at,
                    "maxResults": page_size,
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("issues", []), list):
                raise CuratorError("JIRA_INVALID_JSON", "Jira вернула неожиданный формат поиска.", 502)
            yield data
            issues = data.get("issues", [])
            total = int(data.get("total", len(issues)))
            next_start = int(data.get("startAt", start_at)) + len(issues)
            if not issues or next_start >= total:
                return
            start_at = next_start
        raise CuratorError("JIRA_PAGINATION_LIMIT", "Слишком много страниц Jira; уточните запрос.", 502)

    async def search_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        async for page in self.search_pages(**kwargs):
            issues.extend(page.get("issues", []))
        return issues

    async def employee_list(self, *, billable_only: bool = False, query: str | None = None, page_size: int = 50) -> list[dict[str, Any]]:
        status = " AND status = Billable" if billable_only else ""
        # User query is applied locally; it never enters JQL.
        jql = f"Curators = currentUser() AND issuetype = Employee{status} ORDER BY status ASC, summary ASC"
        issues = await self.search_all(jql=jql, fields=["summary", "status", "issuetype"], page_size=page_size)
        if query:
            needle = query.casefold().strip()
            issues = [i for i in issues if needle in str(i.get("key", "")).casefold() or needle in str(i.get("fields", {}).get("summary", "")).casefold()]
        return issues

    async def employee_issue(self, key: str, fields: list[str]) -> dict[str, Any]:
        safe_key = validate_employee_key(key)
        data = await self._request(f"/rest/api/2/issue/{safe_key}", params={"fields": ",".join(fields)})
        if self.config.log_employee_issue:
            logger.info(
                "jira_employee_issue_response",
                extra={
                    "status_code": 200,
                    "content_type": "application/json",
                    "request_path": f"/rest/api/2/issue/{safe_key}",
                    "response_body": _redact_response_text(
                        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                        self.config.token,
                    ),
                    "response_truncated": False,
                },
            )
        return data if isinstance(data, dict) else {}

    async def comments(self, key: str, page_size: int = 50, max_pages: int = 100) -> list[dict[str, Any]]:
        safe_key = validate_employee_key(key)
        comments: list[dict[str, Any]] = []
        start_at = 0
        for _ in range(max_pages):
            data = await self._request(
                f"/rest/api/2/issue/{safe_key}/comment",
                params={"startAt": start_at, "maxResults": page_size},
            )
            page = data.get("comments", []) if isinstance(data, dict) else []
            if not isinstance(page, list):
                raise CuratorError("JIRA_INVALID_JSON", "Jira вернула неожиданный формат комментариев.", 502)
            comments.extend(page)
            total = int(data.get("total", len(page)))
            next_start = int(data.get("startAt", start_at)) + len(page)
            if not page or next_start >= total:
                return comments
            start_at = next_start
        raise CuratorError("JIRA_PAGINATION_LIMIT", "Слишком много страниц комментариев Jira; уточните запрос.", 502)

    async def child_issues(self, key: str, page_size: int = 50) -> list[dict[str, Any]]:
        safe_key = validate_employee_key(key)
        # Fetch all children and classify locally: issue type names differ between Jira installations.
        # Only a validated key is interpolated. The full JQL remains a query parameter.
        jql = f"parent = {safe_key} ORDER BY created DESC"
        return await self.search_all(
            jql=jql,
            fields=["summary", "status", "created", "updated", "issuetype", "description", "*all"],
            page_size=page_size,
        )


def validate_ca_path(path: str | None) -> str | None:
    if not path:
        return None
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise CuratorError("CA_NOT_FOUND", "Файл корпоративного CA не найден.")
    return str(resolved)
