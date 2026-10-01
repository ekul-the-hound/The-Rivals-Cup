"""Static guard: fail if execution/automation code or dependencies appear. Run in CI and by pytest."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_PATTERNS = [
    r"\b(place|submit|create|modify|cancel|queue|schedule|close)_order\b",
    r"paper_broker|order_simulator|OrderSimulator|PaperBroker",
    r"^\s*(import|from)\s+(selenium|playwright|pyppeteer|pyautogui|telegram|telebot|aiogram"
    r"|apscheduler|celery|schedule|alpaca|ib_insync|ccxt)\b",
]
DEP_PATTERN = r"selenium|playwright|pyppeteer|pyautogui|telegram|telebot|aiogram|apscheduler|celery|alpaca|ib[-_]insync|ccxt"
SQL_PATTERN = r"create\s+table\s+(public\.)?\w*(orders?|executions?|fills?|broker\w*)\b"


def scan() -> list[str]:
    problems: list[str] = []
    for path in list((ROOT / "app").rglob("*.py")) + list(
        (ROOT / "supabase" / "functions").rglob("*")
    ):
        if path.relative_to(ROOT).as_posix() == "app/compliance/rules.py":
            continue  # the audit's own pattern list
        if not path.is_file() or "tests" in path.parts or path.suffix not in {".py", ".ts", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in CODE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
                problems.append(f"{path.relative_to(ROOT)}: matches /{pat}/")
    for path in (ROOT / "supabase" / "migrations").glob("*.sql"):
        if re.search(SQL_PATTERN, path.read_text(), re.IGNORECASE):
            problems.append(f"{path.name}: order/execution-like table")
    pyproject = (ROOT / "pyproject.toml").read_text()
    deps = pyproject.split("[project.optional-dependencies]")[0]
    for m in re.finditer(DEP_PATTERN, deps, re.IGNORECASE):
        problems.append(f"pyproject.toml: prohibited dependency '{m.group(0)}'")
    return problems


if __name__ == "__main__":
    found = scan()
    print("\n".join(found) if found else "OK: no execution/automation code found")
    sys.exit(1 if found else 0)
