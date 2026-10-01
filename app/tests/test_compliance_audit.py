"""Final compliance audit. The real repo must PASS; synthetic repos with each violation must FAIL."""

from pathlib import Path

import pytest

from app.compliance.rules import RULES, audit

ROOT = Path(__file__).resolve().parents[2]


def test_repo_passes_audit():
    rep = audit(ROOT)
    assert rep.ok, rep.render()
    assert rep.files_scanned > 50


def test_audit_covers_every_required_rule():
    assert {r.id for r in RULES} == {
        "BROKER_OR_WSR_CLIENT",
        "TRADERVIEW_CLIENT",
        "BROWSER_AUTOMATION",
        "ORDER_METHODS",
        "EXECUTION_PROVIDER",
        "PAPER_BROKER",
        "SCHEDULED_ORDER_GENERATOR",
        "TRADING_CREDENTIALS",
        "TELEGRAM_EXECUTION",
    }


VIOLATIONS = [
    ("BROKER_OR_WSR_CLIENT", "app/x.py", "import alpaca\n"),
    ("BROKER_OR_WSR_CLIENT", "app/x.py", "class WsrClient:\n    pass\n"),
    ("BROKER_OR_WSR_CLIENT", "app/x.py", "URL = 'https://api.wallstreetrivals.com/v1'\n"),
    ("TRADERVIEW_CLIENT", "app/x.py", "class TraderViewClient:\n    pass\n"),
    ("TRADERVIEW_CLIENT", "app/x.py", "import httpx\nhttpx.get('https://app.traderview.io/x')\n"),
    ("BROWSER_AUTOMATION", "app/x.py", "from selenium import webdriver\n"),
    ("BROWSER_AUTOMATION", "app/x.py", "from playwright.sync_api import sync_playwright\n"),
    ("BROWSER_AUTOMATION", "app/x.js", "const p = require('puppeteer');\n"),
    (
        "BROWSER_AUTOMATION",
        "pyproject.toml",
        '[project]\ndependencies = [\n  "playwright>=1",\n]\n',
    ),
    ("ORDER_METHODS", "app/x.py", "def submit_order(t):\n    pass\n"),
    ("ORDER_METHODS", "app/x.py", "def cancel_order(i):\n    pass\n"),
    ("ORDER_METHODS", "app/x.py", "def close_position(i):\n    pass\n"),
    ("ORDER_METHODS", "supabase/x.sql", "create table public.orders (id int);\n"),
    ("EXECUTION_PROVIDER", "app/x.py", "class ExecutionProvider:\n    pass\n"),
    ("PAPER_BROKER", "app/x.py", "class PaperBroker:\n    pass\n"),
    ("PAPER_BROKER", "app/x.py", "def simulate_fills(o):\n    pass\n"),
    ("SCHEDULED_ORDER_GENERATOR", "app/x.py", "from apscheduler.schedulers import x\n"),
    ("SCHEDULED_ORDER_GENERATOR", "app/x.py", "def generate_orders(p):\n    pass\n"),
    ("TRADING_CREDENTIALS", ".env.example", "BROKER_API_KEY=\n"),
    ("TRADING_CREDENTIALS", ".env.example", "WSR_PASSWORD=\n"),
    ("TRADING_CREDENTIALS", "app/x.py", "TRADERVIEW_TOKEN = 'abc'\n"),
    ("TELEGRAM_EXECUTION", "app/x.py", "import telegram\n"),
    ("TELEGRAM_EXECUTION", "app/x.py", "U = 'https://api.telegram.org/bot/sendMessage'\n"),
    ("TELEGRAM_EXECUTION", ".env.example", "TELEGRAM_BOT_TOKEN=\n"),
]


@pytest.mark.parametrize(("rule", "path", "content"), VIOLATIONS)
def test_each_violation_fails(tmp_path, rule, path, content):
    f = tmp_path / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    rep = audit(tmp_path)
    assert not rep.ok
    assert rule in {x.rule for x in rep.findings}, rep.render()


def test_clean_synthetic_repo_passes(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "ok.py").write_text("def compute_score(x):\n    return x\n")
    assert audit(tmp_path).ok


def test_tests_and_docs_are_not_scanned(tmp_path):
    for d in ("tests", "docs"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "a.py").write_text("def submit_order(): ...\n")
    assert audit(tmp_path).ok


def _mcp_repo(tmp_path, registry: str, extra: str = ""):
    d = tmp_path / "app" / "mcp"
    d.mkdir(parents=True)
    (d / "registry.py").write_text(registry)
    if extra:
        (d / "tools.py").write_text(extra)
    return tmp_path


def test_mcp_write_tool_name_fails(tmp_path):
    _mcp_repo(tmp_path, 'TOOLS = (ToolSpec(\n    "save_note",\n),)\n')
    assert "MCP_WRITE_TOOLS" in {f.rule for f in audit(tmp_path).findings}


def test_mcp_unknown_tool_fails(tmp_path):
    _mcp_repo(tmp_path, 'TOOLS = (ToolSpec("get_portfolio_risk_context"), ToolSpec("get_extra"))\n')
    assert any("get_extra" in f.text for f in audit(tmp_path).findings)


def test_mcp_writer_import_or_upsert_fails(tmp_path):
    _mcp_repo(tmp_path, "TOOLS = ()\n", "from app.db.writer import DB\n")
    assert "MCP_WRITE_TOOLS" in {f.rule for f in audit(tmp_path).findings}
    (tmp_path / "app" / "mcp" / "tools.py").write_text("db.upsert('t', [], 'id')\n")
    assert "MCP_WRITE_TOOLS" in {f.rule for f in audit(tmp_path).findings}


def test_mcp_readonly_hint_false_fails(tmp_path):
    _mcp_repo(tmp_path, "TOOLS = ()\n", '{"readOnlyHint": False}\n')
    assert "MCP_WRITE_TOOLS" in {f.rule for f in audit(tmp_path).findings}
