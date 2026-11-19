from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .errors import CuratorError
from .llm import OllamaClient
from .privacy_filter import PersonalDataFilter, load_dictionary
from .security import read_secret_file

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger("ai_curator.external_llm")


class ExternalOpenAIClient:
    """OpenAI Chat Completions adapter; receives only a sanitized context."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float,
        *,
        temperature: float,
        max_tokens: int,
        log_request_response: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.log_request_response = log_request_response

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 10.0)) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers=self._headers
                )
            return {"ok": response.status_code < 400, "status": response.status_code}
        except httpx.HTTPError:
            return {"ok": False, "status": None}

    def _request_payload(
        self, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
            "stream": False,
            "store": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "one_on_one",
                    "strict": True,
                    "schema": schema,
                },
            },
        }

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
                    "request_body": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            request_task = asyncio.create_task(
                client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
            )
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, pending = await asyncio.wait(
                {request_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if cancel_task in done and cancel_event.is_set():
                request_task.cancel()
                raise CuratorError("GENERATION_CANCELLED", "Генерация отменена.", 409)
            try:
                response = request_task.result()
            except httpx.TimeoutException as exc:
                raise CuratorError(
                    "EXTERNAL_LLM_TIMEOUT",
                    "Внешняя модель не ответила вовремя.",
                    504,
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
            content = message["content"]
            usage = response_payload.get("usage") or {}
            logger.info(
                "external_llm_response_metadata",
                extra={
                    "operation": operation,
                    "finish_reason": choice.get("finish_reason"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                },
            )
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                raise TypeError("structured response must be an object")
            return parsed
        except CuratorError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CuratorError(
                "EXTERNAL_LLM_INVALID_JSON",
                "Внешняя модель вернула некорректный JSON.",
                502,
            ) from exc

    async def generate(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        return await self._once(messages, schema, cancel_event, operation="generate")

    async def extract_comment_events(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        return await self._once(
            messages, schema, cancel_event, operation="comment_extraction"
        )

    async def repair(
        self,
        messages: list[dict[str, str]],
        bad_result: dict[str, Any],
        schema: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": json.dumps(bad_result, ensure_ascii=False)},
            {
                "role": "user",
                "content": (
                    "Исправь только структуру ответа по JSON Schema и инварианты. "
                    "Не добавляй новые факты. Верни только JSON."
                ),
            },
        ]
        return await self._once(
            repair_messages, schema, cancel_event, operation="repair"
        )


@dataclass(frozen=True)
class ExternalRuntimeBundle:
    llm: ExternalOpenAIClient
    privacy_filter: PersonalDataFilter


def build_privacy_filter(settings: Settings, root: Path) -> PersonalDataFilter:
    """Build the mandatory local privacy boundary used by every main runtime."""
    dictionary_terms = load_dictionary(settings.pii_filter_dictionary_file)
    pii_model = OllamaClient(
        settings.pii_filter_base_url,
        settings.pii_filter_model,
        settings.pii_filter_timeout_seconds,
        temperature=0.0,
        max_tokens=2000,
        reasoning_effort="none",
        # The PII detector receives raw text locally. Never copy that payload to logs.
        log_request_response=False,
    )
    privacy_filter = PersonalDataFilter(
        pii_model,
        dictionary_terms,
        root / "prompts/pii_filter.system.txt",
        batch_items=settings.pii_filter_batch_items,
        batch_chars=settings.pii_filter_batch_chars,
    )
    return privacy_filter


def build_external_runtime(
    settings: Settings,
    root: Path,
    privacy_filter: PersonalDataFilter | None = None,
) -> ExternalRuntimeBundle:
    api_key = read_secret_file(settings.external_llm_api_key_file)
    if not api_key:
        raise CuratorError(
            "EXTERNAL_LLM_KEY_MISSING",
            "Не найден API-ключ внешней модели в файле секрета.",
            500,
        )
    privacy_filter = privacy_filter or build_privacy_filter(settings, root)
    llm = ExternalOpenAIClient(
        settings.external_llm_base_url,
        settings.external_llm_model,
        api_key,
        settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        log_request_response=settings.llm_log_request_response,
    )
    return ExternalRuntimeBundle(llm, privacy_filter)
