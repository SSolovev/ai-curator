# Выбор локальной модели и benchmark

Дата проверки источников: 2026-08-12. Все три модели — instruct/post-trained open-weight модели, работающие в `llama.cpp`; runtime в Compose закреплён как `b9445` и digest. Слово «open» не отменяет проверки лицензии юридической службой.

## Сравнение

| Роль | Точная модель | Лицензия | Параметры / GGUF | Контекст | Оценка памяти при 16K | Русский и JSON | Оценка скорости* |
|---|---|---|---|---|---|---|---|
| Быстрая | `Qwen/Qwen3-4B`, Q4_K_M | Apache-2.0; корпоративное/коммерческое использование допускается с notices | 4B; 2.50 GB | 32K native; расширение YaRN возможно | RAM 4–6 GB; VRAM/UMA около 4–6 GB | русский входит в 119 языков; схема обычно требует constrained decoding | CPU 8–20 tok/s; integrated GPU 15–35 tok/s |
| Сбалансированная, default | `Qwen/Qwen3-8B`, Q4_K_M, official GGUF | Apache-2.0; notices/условия Apache | 8.2B; 5.03 GB; SHA-256 `d98cdc…5785` | 32,768 native; 131,072 с YaRN | RAM 7–10 GB; VRAM/UMA около 7–10 GB | сильный multilingual/Russian и instruction following; хороший компромисс для strict JSON | CPU 4–10 tok/s; integrated/low GPU 8–25 tok/s |
| Качественная, медленная | `mistralai/Mistral-Small-3.2-24B-Instruct-2506`, Q4_K_M | Apache-2.0 | 24B; community GGUF 14.33 GB | 131,072 | RAM 18–23 GB; VRAM/UMA около 18–23 GB | 24 языка, включая русский; релиз улучшает instruction following и repetition | CPU 1.5–4 tok/s; подходящий GPU/UMA 6–20 tok/s |

\* Все диапазоны скорости и памяти — инженерные оценки, не измерения на ноутбуке пользователя. Они сильно зависят от CPU, RAM bandwidth, backend, prompt length и offload. 32 ГБ системной RAM не означают 32 ГБ VRAM. Для рабочего запроса приложение ограничивает runtime 16K, хотя модели поддерживают больше.

### Почему default — Qwen3-8B Q4_K_M

Он занимает 5.03 GB на диске, оставляет большой запас в 32 GB RAM для Docker/Windows/Jira данных, официально поддерживает русский среди 119 языков и Apache-2.0 проще для корпоративной проверки. 4B быстрее, но чаще теряет нюансы и source discipline; 24B потенциально качественнее, но на CPU может сделать подготовку слишком долгой и оставляет меньше RAM.

Qwen3 имеет thinking/non-thinking modes. Для этого сценария constrained JSON важнее длинного reasoning trace; prompt короткий, temperature низкая. Перед production pilot нужно измерить качество русского на корпоративно одобренном обезличенном наборе.

## Источники

- [Qwen3 official announcement](https://qwenlm.github.io/blog/qwen3/) — Apache-2.0, размеры, 119 языков и контекст;
- [Qwen3-8B official model card](https://huggingface.co/Qwen/Qwen3-8B) — 8.2B, native 32,768/YaRN 131,072;
- [Qwen3-8B official GGUF file](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/212c964b8f97cb5edc203d411b767aaae707e653/Qwen3-4B-Q4_K_M.gguf) — 5.03 GB и SHA-256;
- [Qwen3-4B official GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF) — quant sizes и llama.cpp guidance;
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

