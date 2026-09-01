#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
.venv/bin/python -m pytest --cov=ai_curator --cov-report=term-missing
.venv/bin/python -m ruff check src tests mock_jira

