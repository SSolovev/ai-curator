from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def memory_bytes(raw: str) -> int | None:
    try:
        left = raw.split("/")[0].strip()
        number = float("".join(c for c in left if c.isdigit() or c == "."))
        unit = "".join(c for c in left if c.isalpha()).lower()
        return int(number * {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "kb": 1000, "mb": 1000**2, "gb": 1000**3}.get(unit, 1))
    except (ValueError, IndexError):
        return None


def sample_memory(stop: threading.Event, samples: list[int], container_name: str) -> None:
    docker = shutil.which("docker")
    if not docker:
        return
    while not stop.wait(0.25):
        result = subprocess.run(  # noqa: S603 - fixed executable and argument list, no shell
            [docker, "stats", "--no-stream", "--format", "{{.MemUsage}}", container_name],
            capture_output=True, text=True, check=False,
        )
        value = memory_bytes(result.stdout.strip())
        if value is not None:
            samples.append(value)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18081")
    parser.add_argument("--container", default="ai-curator-model-benchmark")
    args = parser.parse_args()
    load_start = time.perf_counter()
    async with httpx.AsyncClient(timeout=300) as client:
        for _ in range(600):
            try:
                if (await client.get(f"{args.url}/health")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
        else:
            raise SystemExit("model health timeout")
        load_seconds = time.perf_counter() - load_start
        context = json.loads((ROOT / "tests/fixtures/benchmark_context.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schemas/one_on_one.response.schema.v1.json").read_text(encoding="utf-8"))
        system = (ROOT / "prompts/one_on_one.system.txt").read_text(encoding="utf-8")
        payload = {
            "model": "Qwen3-4B-Q4_K_M.gguf", "stream": True, "temperature": 0.2, "max_tokens": 5000,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"Подготовь сценарий следующей встречи 1:1.\n<employee_context>\n{json.dumps(context, ensure_ascii=False)}\n</employee_context>"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "one_on_one", "strict": True, "schema": schema}},
        }
        stop, samples = threading.Event(), []
        sampler = threading.Thread(target=sample_memory, args=(stop, samples, args.container), daemon=True)
        sampler.start()
        start, first, content, usage = time.perf_counter(), None, "", {}
        async with client.stream("POST", f"{args.url}/v1/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                event = json.loads(line[6:])
                delta = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta and first is None:
                    first = time.perf_counter()
                content += delta
                usage = event.get("usage") or usage
        elapsed = time.perf_counter() - start
        stop.set()
        sampler.join(timeout=2)
    parsed = None
    try:
        parsed = json.loads(content)
        schema_valid = not list(Draft202012Validator(schema).iter_errors(parsed))
    except json.JSONDecodeError:
        schema_valid = False
    quality = {
        "agenda_exactly_30": bool(parsed and parsed.get("agenda") and parsed["agenda"][0]["start_minute"] == 0 and parsed["agenda"][-1]["end_minute"] == 30),
        "priorities_le_4": bool(parsed and len(parsed.get("meeting_priorities", [])) <= 4),
        "source_refs_from_input": bool(parsed) and all(ref in json.dumps(context) for ref in [e.get("source_ref", "") for p in parsed.get("meeting_priorities", []) for e in p.get("evidence", [])]),
    }
    tokens = usage.get("completion_tokens")
    report = {
        "measured": True, "model": "Qwen3-4B-Q4_K_M.gguf", "runtime": "llama.cpp b9445",
        "load_seconds": round(load_seconds, 3), "peak_container_memory_bytes": max(samples) if samples else None,
        "ttft_seconds": round((first - start), 3) if first else None, "elapsed_seconds": round(elapsed, 3),
        "completion_tokens": tokens, "tokens_per_second": round(tokens / elapsed, 3) if tokens else None,
        "json_parse_valid": parsed is not None, "json_schema_valid": schema_valid, "quality_checks": quality,
        "note": "Измерено этим запуском на текущем ноутбуке; повторите минимум три раза.",
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/model-benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
