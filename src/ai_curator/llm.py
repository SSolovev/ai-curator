from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

import httpx

from .errors import CuratorError

logger = logging.getLogger("ai_curator.llm")


class LocalLLM(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def generate(
        self, messages: list[dict[str, str]], schema: dict[str, Any], cancel_event: asyncio.Event
    ) -> dict[str, Any]: ...


class LlamaCppClient:
    """OpenAI-compatible llama.cpp server adapter with native JSON-schema grammar."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 240.0,
        *,
        temperature: float = 0.2,
        max_tokens: int = 6000,
        log_request_response: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.log_request_response = log_request_response

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/health")
            return {"ok": response.status_code == 200, "status": response.status_code}
        except httpx.HTTPError:
            return {"ok": False, "status": None}

    def _request_payload(
        self, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "one_on_one", "strict": True, "schema": schema},
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
            logger.info(
                "llm_request_payload",
                extra={
                    "operation": operation,
                    "request_body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            request_task = asyncio.create_task(client.post(f"{self.base_url}/v1/chat/completions", json=payload))
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, pending = await asyncio.wait({request_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if cancel_task in done and cancel_event.is_set():
                request_task.cancel()
                raise CuratorError("GENERATION_CANCELLED", "Генерация отменена.", 409)
            try:
                response = request_task.result()
            except httpx.TimeoutException as exc:
                raise CuratorError("LLM_TIMEOUT", "Локальная модель не ответила вовремя.", 504) from exc
            except httpx.HTTPError as exc:
                raise CuratorError("LLM_NETWORK", "Локальная модель недоступна. Откройте диагностику.", 502) from exc
        if self.log_request_response:
            logger.info(
                "llm_response_payload",
                extra={
                    "operation": operation,
                    "status_code": response.status_code,
                    "response_body": response.text,
                },
            )
        if response.status_code >= 400:
            raise CuratorError("LLM_HTTP_ERROR", "Локальная модель вернула ошибку.", 502)
        try:
            response_payload = response.json()
            choice = response_payload["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            usage = response_payload.get("usage") or {}
            logger.info(
                "llm_response_metadata",
                extra={
                    "operation": operation,
                    "finish_reason": finish_reason,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                },
            )
            if finish_reason == "length":
                raise CuratorError(
                    "LLM_OUTPUT_TRUNCATED",
                    "Локальная модель исчерпала лимит токенов до формирования полного ответа.",
                    502,
                )
            if content is None or (isinstance(content, str) and not content.strip()):
                raise CuratorError(
                    "LLM_EMPTY_RESPONSE",
                    "Локальная модель не сформировала итоговый ответ.",
                    502,
                )
            return json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise CuratorError("LLM_INVALID_JSON", "Локальная модель вернула некорректный JSON.", 502) from exc

    async def generate(self, messages: list[dict[str, str]], schema: dict[str, Any], cancel_event: asyncio.Event) -> dict[str, Any]:
        return await self._once(messages, schema, cancel_event, operation="generate")

    async def extract_comment_events(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        return await self._once(messages, schema, cancel_event, operation="comment_extraction")

    async def repair(
        self, messages: list[dict[str, str]], bad_result: dict[str, Any], schema: dict[str, Any], cancel_event: asyncio.Event
    ) -> dict[str, Any]:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": json.dumps(bad_result, ensure_ascii=False)},
            {"role": "user", "content": "Исправь только структуру ответа по JSON Schema и инварианты. Не добавляй новые факты. Верни только JSON."},
        ]
        return await self._once(repair_messages, schema, cancel_event, operation="repair")


class OllamaClient(LlamaCppClient):
    """Native Ollama adapter using its local OpenAI-compatible generation API."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 240.0,
        *,
        temperature: float = 0.2,
        max_tokens: int = 6000,
        reasoning_effort: str = "none",
        log_request_response: bool = False,
    ):
        super().__init__(
            base_url,
            model,
            timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            log_request_response=log_request_response,
        )
        self.reasoning_effort = reasoning_effort

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 10.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            if response.status_code >= 400:
                return {"ok": False, "status": response.status_code, "model_available": False}
            payload = response.json()
            if not isinstance(payload, dict):
                return {"ok": False, "status": response.status_code, "model_available": False}
            available_models = {
                name
                for item in payload.get("models", [])
                if isinstance(item, dict)
                for name in (item.get("name"), item.get("model"))
                if isinstance(name, str)
            }
            configured_names = {self.model}
            if ":" not in self.model:
                configured_names.add(f"{self.model}:latest")
            elif self.model.endswith(":latest"):
                configured_names.add(self.model.removesuffix(":latest"))
            model_available = bool(configured_names & available_models)
            return {
                "ok": model_available,
                "status": response.status_code,
                "model_available": model_available,
            }
        except (httpx.HTTPError, TypeError, ValueError):
            return {"ok": False, "status": None, "model_available": False}

    def _request_payload(
        self, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> dict[str, Any]:
        payload = super()._request_payload(messages, schema)
        # Gemma 3 is not a thinking model in Ollama. Sending an enabled
        # reasoning_effort to a model without that capability can be rejected.
        normalized_model = self.model.casefold()
        if "gemma3" not in normalized_model and "gemma-3" not in normalized_model:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload
