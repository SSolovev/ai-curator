from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

import httpx

from .errors import CuratorError


class LocalLLM(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def generate(
        self, messages: list[dict[str, str]], schema: dict[str, Any], cancel_event: asyncio.Event
    ) -> dict[str, Any]: ...


class LlamaCppClient:
    """OpenAI-compatible llama.cpp server adapter with native JSON-schema grammar."""

    def __init__(self, base_url: str, model: str, timeout: float = 240.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/health")
            return {"ok": response.status_code == 200, "status": response.status_code}
        except httpx.HTTPError:
            return {"ok": False, "status": None}

    async def _once(
        self, messages: list[dict[str, str]], schema: dict[str, Any], cancel_event: asyncio.Event
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 3000,
            "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "one_on_one", "strict": True, "schema": schema}},
        }
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
        if response.status_code >= 400:
            raise CuratorError("LLM_HTTP_ERROR", "Локальная модель вернула ошибку.", 502)
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise CuratorError("LLM_INVALID_JSON", "Локальная модель вернула некорректный JSON.", 502) from exc

    async def generate(self, messages: list[dict[str, str]], schema: dict[str, Any], cancel_event: asyncio.Event) -> dict[str, Any]:
        return await self._once(messages, schema, cancel_event)

    async def repair(
        self, messages: list[dict[str, str]], bad_result: dict[str, Any], schema: dict[str, Any], cancel_event: asyncio.Event
    ) -> dict[str, Any]:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": json.dumps(bad_result, ensure_ascii=False)},
            {"role": "user", "content": "Исправь только структуру ответа по JSON Schema и инварианты. Не добавляй новые факты. Верни только JSON."},
        ]
        return await self._once(repair_messages, schema, cancel_event)
