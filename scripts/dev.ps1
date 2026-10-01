# Lint + test + guard, then run the API. Usage: .\scripts\dev.ps1
$ErrorActionPreference = "Stop"
ruff check .
ruff format --check .
pytest
python scripts/check_no_execution.py
uvicorn app.api.main:app --reload --port 8000
