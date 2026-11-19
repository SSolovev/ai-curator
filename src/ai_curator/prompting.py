from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_messages(context: dict[str, Any], system_path: Path) -> list[dict[str, str]]:
    system = system_path.read_text(encoding="utf-8").strip()
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    user = f"Подготовь сценарий следующей встречи 1:1.\n\n<employee_context>\n{payload}\n</employee_context>"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
