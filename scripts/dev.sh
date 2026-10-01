#!/usr/bin/env bash
set -euo pipefail
ruff check . && ruff format --check . && pytest && python scripts/check_no_execution.py
exec uvicorn app.api.main:app --reload --port 8000
