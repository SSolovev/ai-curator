from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from .errors import CuratorError
from .llm import ChatCompletionsClient
from .security import read_secret_file

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger("ai_curator.external_llm")
YANDEX_AI_STUDIO_URL = "https://ai.api.cloud.yandex.net/v1"


class ExternalOpenAIClient(ChatCompletionsClient):
    """OpenAI Chat Completions adapter; receives only a sanitized context."""

    completion_limit_key = "max_completion_tokens"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float,
        *,
        temperature: float,
        max_tokens: int,
        reasoning_effort: str = "none",
        log_request_response: bool = False,
    ) -> None:
        super().__init__(
            base_url,
            model,
            timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            log_request_response=log_request_response,
        )
        self._api_key = api_key
        self._yandex_folder_id: str | None = None
        self._disable_qwen_reasoning = False
        if self.base_url == YANDEX_AI_STUDIO_URL:
            uri = urlsplit(model)
            if (
                uri.scheme != "gpt"
                or not uri.netloc.isascii()
                or not uri.netloc.isalnum()
                or not uri.path.strip("/")
                or uri.query
                or uri.fragment
            ):
                raise CuratorError(
                    "EXTERNAL_LLM_MODEL_INVALID",
                    "Укажите URI модели Яндекса в формате gpt://ID_КАТАЛОГА/имя-модели.",
                    500,
                )
            self._yandex_folder_id = uri.netloc
            is_qwen = uri.path.lstrip("/").casefold().startswith("qwen")
            self._disable_qwen_reasoning = is_qwen and reasoning_effort == "none"

    def _headers(self, *, for_model_list: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._yandex_folder_id:
            headers["Authorization"] = f"Api-Key {self._api_key}"
            project_header = "x-project" if for_model_list else "OpenAI-Project"
            headers[project_header] = self._yandex_folder_id
            if not for_model_list:
                headers["x-data-logging-enabled"] = "false"
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 10.0)) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers=self._headers(for_model_list=True)
                )
            return {"ok": response.status_code < 400, "status": response.status_code}
        except httpx.HTTPError:
            return {"ok": False, "status": None}

    def _request_payload(
        self, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        payload = super()._request_payload(messages, schema)
        payload["store"] = False
        if self._disable_qwen_reasoning:
            payload["reasoning_effort"] = "none"
        return payload

    async def _once(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        cancel_event: asyncio.Event,
        *,
        operation: str,
    ) -> dict[str, Any]:
        payload = self._request_payload(messages, schema)
        if self.log_request_response:
            # At this boundary messages have already passed the local PII and
            # dictionary filters. Authorization headers are never logged.
            logger.info(
                "external_llm_request_payload",
                extra={
                    "operation": operation,
                    "request_body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            )
        try:
            response = await self._post_completion(
                f"{self.base_url}/chat/completions", payload, cancel_event, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise CuratorError(
                "EXTERNAL_LLM_TIMEOUT", "Внешняя модель не ответила вовремя.", 504
            ) from exc
        except httpx.HTTPError as exc:
            raise CuratorError(
                "EXTERNAL_LLM_NETWORK",
                "Внешняя модель недоступна. Откройте диагностику.",
                502,
            ) from exc
        if self.log_request_response:
            logger.info(
                "external_llm_response_payload",
                extra={
                    "operation": operation,
                    "status_code": response.status_code,
                    "response_body": response.text,
                },
            )
        if response.status_code >= 400:
            raise CuratorError(
                "EXTERNAL_LLM_HTTP_ERROR",
                "Внешняя модель вернула ошибку. Проверьте ключ, модель и диагностику.",
                502,
            )
        try:
            response_payload = response.json()
            choice = response_payload["choices"][0]
            message = choice["message"]
            refusal = message.get("refusal")
            if refusal:
                raise CuratorError(
                    "EXTERNAL_LLM_REFUSED",
                    "Внешняя модель отказалась сформировать сценарий.",
                    502,
                )
            finish_reason = choice.get("finish_reason")
            usage = response_payload.get("usage") or {}
            logger.info(
                "external_llm_response_metadata",
                extra={
                    "operation": operation,
                    "finish_reason": finish_reason,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                },
            )
            if finish_reason == "length":
                raise CuratorError(
                    "EXTERNAL_LLM_OUTPUT_TRUNCATED",
                    "Внешняя модель исчерпала лимит токенов до завершения ответа.",
                    502,
                )
            content = message.get("content")
            if content is None or (isinstance(content, str) and not content.strip()):
                raise CuratorError(
                    "EXTERNAL_LLM_EMPTY_RESPONSE",
                    "Внешняя модель не сформировала итоговый ответ.",
                    502,
                )
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                raise TypeError("structured response must be an object")
            return parsed
        except (KeyError, IndexError, AttributeError, TypeError, ValueError) as exc:
            raise CuratorError(
                "EXTERNAL_LLM_INVALID_JSON",
                "Внешняя модель вернула некорректный JSON.",
                502,
            ) from exc


def build_external_client(
    settings: Settings,
) -> ExternalOpenAIClient:
    api_key = read_secret_file(settings.external_llm_api_key_file)
    if not api_key:
        raise CuratorError(
            "EXTERNAL_LLM_KEY_MISSING",
            "Не найден API-ключ внешней модели в файле секрета.",
            500,
        )
    return ExternalOpenAIClient(
        settings.external_llm_base_url,
        settings.external_llm_model,
        api_key,
        settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
        log_request_response=settings.llm_log_request_response,
    )
