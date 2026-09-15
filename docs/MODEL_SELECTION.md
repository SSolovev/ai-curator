# Выбор локальной модели и benchmark

Дата проверки источников: 2026-09-14. Модели — instruct/post-trained open-weight модели. Qwen3 4B работает в закреплённом `llama.cpp` Compose runtime; основная Gemma 4 12B и альтернативная Qwen3 8B поддерживаются через native Ollama. Слово «open» не отменяет проверки лицензии юридической службой.

## Сравнение

| Роль | Точная модель | Лицензия | Параметры / GGUF | Контекст | Оценка памяти при 16K | Русский и JSON | Оценка скорости* |
|---|---|---|---|---|---|---|---|
| Быстрая | `Qwen/Qwen3-4B`, Q4_K_M | Apache-2.0; корпоративное/коммерческое использование допускается с notices | 4B; 2.50 GB | 32K native; расширение YaRN возможно | RAM 4–6 GB; VRAM/UMA около 4–6 GB | русский входит в 119 языков; схема обычно требует constrained decoding | CPU 8–20 tok/s; integrated GPU 15–35 tok/s |
| Более лёгкая, для Ollama | `Qwen/Qwen3-8B`, Q4_K_M, official GGUF | Apache-2.0; notices/условия Apache | 8.2B; 5.03 GB; SHA-256 `d98cdc…5785` | 32,768 native; 131,072 с YaRN | RAM 7–10 GB; VRAM/UMA около 7–10 GB | сильный multilingual/Russian и instruction following; хороший компромисс для strict JSON | CPU 4–10 tok/s; integrated/low GPU 8–25 tok/s |
| Основная, default | `gemma4:12b`, Q4_K_M | Apache-2.0 | 12B; пакет Ollama около 7.6 GB | 256K maximum; профиль проекта использует 16K | официальный минимум загрузки Q4 около 6.7 GB; с контекстом и runtime нужен дополнительный запас | 140+ языков, native system role, thinking и structured JSON | на MacBook Air M3 измерить benchmark; ожидаемо медленнее Qwen3 8B |
| Качественная, медленная | `mistralai/Mistral-Small-3.2-24B-Instruct-2506`, Q4_K_M | Apache-2.0 | 24B; community GGUF 14.33 GB | 131,072 | RAM 18–23 GB; VRAM/UMA около 18–23 GB | 24 языка, включая русский; релиз улучшает instruction following и repetition | CPU 1.5–4 tok/s; подходящий GPU/UMA 6–20 tok/s |

\* Все диапазоны скорости и памяти — инженерные оценки, не измерения на ноутбуке пользователя. Они сильно зависят от CPU, RAM bandwidth, backend, prompt length и offload. На MacBook Air M3 с 16 ГБ unified memory Gemma 4 запускается только в квантизованном варианте и с ограниченным контекстом; заявленные 256K для этой конфигурации непрактичны.

### Почему default — Gemma 4 12B Q4_K_M

Gemma 4 лучше соответствует задаче проекта: нативно поддерживает системную роль, thinking и structured JSON, а Q4_K_M-пакет Ollama размером около 7,6 ГБ помещается в 16 ГБ unified memory. Профиль ограничен 16K, чтобы оставить память macOS, Docker и KV-кешу. Qwen3 8B остаётся более быстрой альтернативой при высокой memory pressure.

Gemma 4 и Qwen3 имеют thinking modes. Для извлечения событий и синтеза используется `reasoning_effort=none`: на Gemma 4 даже профиль `low` способен израсходовать весь лимит ответа на reasoning и оставить пустой JSON. Перед production pilot обе модели нужно сравнить на корпоративно одобренном обезличенном наборе.

Runtime `external` использует ещё один локальный тег `ai-curator-pii-gemma3-4b` с контекстом 8K. Это специализированный предварительный фильтр, а не модель подготовки сценария; он создаётся отдельной командой `./scripts/prepare-ollama.sh pii-filter` и дополняется детерминированным словарём и regex.

## Источники

- [Qwen3 official announcement](https://qwenlm.github.io/blog/qwen3/) — Apache-2.0, размеры, 119 языков и контекст;
- [Qwen3-8B official model card](https://huggingface.co/Qwen/Qwen3-8B) — 8.2B, native 32,768/YaRN 131,072;
- [Qwen3-8B official GGUF file](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/212c964b8f97cb5edc203d411b767aaae707e653/Qwen3-4B-Q4_K_M.gguf) — 5.03 GB и SHA-256;
- [Qwen3-4B official GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF) — quant sizes и llama.cpp guidance;
- [Gemma 4 в каталоге Ollama](https://ollama.com/library/gemma4) — официальный тег `gemma4:12b`, Q4_K_M-пакет и варианты модели;
- [Gemma 4 model overview](https://ai.google.dev/gemma/docs/core) — возможности, память, structured output и контекст;
- [Mistral Small 3.2 official card](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506) — Apache-2.0, 24 languages, improved instruction following;
- [Mistral config](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506/blob/main/config.json) — 131,072 positions;
- [Mistral Q4_K_M GGUF information](https://huggingface.co/bartowski/mistralai_Mistral-Small-3.2-24B-Instruct-2506-GGUF) — 14.33 GB community quant;
- [llama.cpp Docker/server documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md) — local server images and acceleration variants.

## Воспроизводимый smoke benchmark

После `prepare-model`:

```bash
./scripts/start.sh
./scripts/benchmark-model.sh
```

Скрипт перезапускает только model container, измеряет время до health, пик container memory во время запроса, TTFT, completion tokens/sec, parse/JSON Schema validity и структурные quality checks на вымышленном fixture. Результат пишется в `reports/model-benchmark.json`; он не содержит Jira PAT/реальных данных.

Не сравнивайте цифры из таблицы с результатом как будто это один benchmark: таблица — оценка, JSON report — измерение конкретного запуска. Для выбора 4B/24B замените файл, checksum, Compose model name и повторите один и тот же harness минимум три раза после warm-up.
