# Third-party notices

Проектный код предоставлен для внутренней оценки; отдельная лицензия владельца репозитория не была указана. До распространения организация должна назначить лицензию на собственный код.

Основные third-party components:

- Python 3.12 — Python Software Foundation License;
- FastAPI — MIT;
- Pydantic / pydantic-settings — MIT;
- httpx — BSD-3-Clause;
- jsonschema — MIT;
- Uvicorn — BSD-3-Clause;
- llama.cpp `b9445` — MIT; image digest закреплён в `compose.yaml`;
- Qwen3-8B and official Q4_K_M GGUF — Apache License 2.0, copyright Alibaba Cloud/Qwen contributors. Model weights не включены в repository/application image; staging script скачивает их отдельно с official repository and verifies SHA-256;
- Qwen3-4B — Apache License 2.0 (alternative only);
- Mistral-Small-3.2-24B-Instruct-2506 — Apache License 2.0 (alternative only); suggested GGUF is third-party quantization and must retain upstream/quantizer notices.

Полные license texts и notices следует получить из закреплённых source distributions перед корпоративным распространением offline bundle. Apache-2.0 допускает коммерческое использование при соблюдении условий notices/license; итоговую оценку делает юридическая служба.
