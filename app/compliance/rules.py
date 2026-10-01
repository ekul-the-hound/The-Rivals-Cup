"""Static compliance audit over the repository's own code, config and SQL.

Pure text/regex scanning; it imports nothing from the scanned code. The audit FAILS (non-empty
findings) if the repo contains any of: broker or WSR clients, Trader View clients, browser
automation, order-method names, an execution provider, a paper broker / fill simulator, a
scheduled-order generator, trading credentials, Telegram handlers, or MCP write tools.

Excluded from scanning: tests (they contain deliberate violation fixtures), docs, VCS/caches, and
this file + the older guard script (which necessarily spell out the patterns).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".js",
    ".sql",
    ".toml",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".sh",
    ".ps1",
    ".cfg",
    ".ini",
}
CODE_NAMES = {".env.example", ".env", "Dockerfile"}
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "docs",
    "tests",
    ".mypy_cache",
}
SELF_EXCLUDED = {"app/compliance/rules.py", "scripts/check_no_execution.py"}

ALLOWED_MCP_TOOLS = frozenset(
    {
        "get_competition_rules_summary",
        "get_market_dashboard",
        "rank_sector_etfs",
        "get_peer_pair_candidates",
        "get_peer_pair_packet",
        "get_stock_research_packet",
        "search_sec_filings",
        "search_news",
        "get_liquidity_check",
        "get_portfolio_risk_context",
        "get_weekly_event_blackout_list",
        "get_manual_entry_checklist",
    }
)

_VERBS = r"submit|place|cancel|modify|replace|amend|create|send|route|execute|close|flatten|liquidate|queue|schedule|fire|transmit"
_NOUNS = r"orders?|trades?|positions?"
_BROKERS = r"alpaca|ib_insync|ibapi|tradier|robin_stocks|robinhood|schwab|tda|td_ameritrade|tradestation|webull|ccxt|polygon_trading|etrade|fidelity"
_BROWSER = r"selenium|playwright|pyppeteer|puppeteer|pyautogui|mechanize|splinter|undetected_chromedriver|seleniumbase|helium|pywinauto|robotframework"
_SCHED = r"apscheduler|celery|schedule|rq_scheduler|dramatiq|huey|crontab|croniter"
_TG = r"telegram|telebot|aiogram|pyrogram|telethon|python_telegram_bot"


def _imp(mods: str) -> str:
    return rf"^\s*(?:import|from)\s+(?:{mods})\b"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    patterns: tuple[str, ...]
    suffixes: frozenset[str] | None = None  # None = all scanned files


RULES: tuple[Rule, ...] = (
    Rule(
        "BROKER_OR_WSR_CLIENT",
        "Broker or Wall Street Rivals client",
        (
            _imp(_BROKERS),
            rf"\b(?:{_BROKERS})[-_]?(?:trade|api|client|py)\b",
            r"\bclass\s+\w*(?:Broker|Brokerage|WSR|WallStreetRivals)\w*",
            r"\w*(?:Broker|Brokerage|Wsr|WSR|WallStreetRivals)(?:Client|Session|Api|API|Adapter|Gateway)\b",
            r"wallstreetrivals|wall-street-rivals|wsr(?:rivals)?\.(?:com|io|net|app)\b",
            r"https?://[^\s\"']*(?:alpaca\.markets|tradier\.com|interactivebrokers|schwabapi)",
        ),
    ),
    Rule(
        "TRADERVIEW_CLIENT",
        "Trader View client or integration",
        (
            r"\b\w*TraderView\w*",
            r"\btrader_?view_?\w*",
            r"traderview\.",
            r"https?://[^\s\"']*trader-?view",
        ),
    ),
    Rule(
        "BROWSER_AUTOMATION",
        "Selenium / Playwright / Puppeteer / browser automation",
        (
            _imp(_BROWSER),
            rf"(?i)(?:require|import)\s*\(?\s*[\"'](?:{_BROWSER})[\"']",
            r"\b(?:sync_playwright|async_playwright|webdriver\.\w+|chromedriver|geckodriver|headless[_ ]?browser)\b",
            r"\bchromium\.launch|\bbrowser\.new_page\b|\bpage\.(?:click|goto|fill)\(",
            rf"(?im)^\s*[\"']?(?:{_BROWSER})(?:[\"']?\s*[:=<>~!,]|[\"']?\s*$)",
        ),
    ),
    Rule(
        "ORDER_METHODS",
        "submit_order / cancel_order / close_position style methods",
        (
            rf"\b(?:{_VERBS})_(?:{_NOUNS})\b",
            rf"\b(?:{_VERBS})(?:Order|Trade|Position)s?\b",
            r"(?i)create\s+table\s+(?:public\.)?\w*(?:orders?|executions?|fills?|brokerage?\w*)\b",
        ),
    ),
    Rule(
        "EXECUTION_PROVIDER",
        "Execution provider / order router",
        (
            r"\b\w*(?:ExecutionProvider|ExecutionClient|ExecutionEngine|ExecutionService|OrderRouter|OrderManager|OrderGateway|OrderEngine)\b",
            r"\b(?:execution_provider|execution_client|execution_engine|order_router|order_manager|order_gateway|order_engine|smart_route)\b",
        ),
    ),
    Rule(
        "PAPER_BROKER",
        "Paper broker / order or fill simulator",
        (
            r"(?i)\b(?:paper_?broker|paper_?trad\w*|order_?simulator|fill_?simulator|execution_?simulator|matching_?engine)\b",
            r"\b(?:simulate|emulate)_(?:orders?|fills?|trades?|stops?)\b",
            r"\b(?:Paper|Fill|Order|Stop)Simulator\b",
        ),
    ),
    Rule(
        "SCHEDULED_ORDER_GENERATOR",
        "Scheduled / automatic order generator",
        (
            _imp(_SCHED),
            r"(?i)\b(?:scheduled_orders?|order_schedule\w*|generate_orders?|order_generator|trade_scheduler|auto_?trad\w*|autotrader|trade_bot|trading_bot)\b",
            r"\bOrderGenerator\b|\bAutoTrader\b",
            r"(?i)\bcron\.schedule\b|\bpg_cron\b",
        ),
    ),
    Rule(
        "TRADING_CREDENTIALS",
        "Trading / WSR / Trader View credentials",
        (
            r"(?i)\b\w*(?:broker|alpaca|tradier|ibkr|wsr|trader_?view|rivals?|robinhood|schwab|brokerage)\w*_(?:api_?key|secret|token|password|passwd|username|login|credentials?)\w*\b",
            r"(?i)\b(?:broker|alpaca|tradier|ibkr|wsr|trader_?view)_?(?:key|secret|token|password|user)\b",
            r"(?i)\b(?:wsr|trader_?view)\w*\s*[:=]\s*[\"'][^\"']{4,}",
        ),
    ),
    Rule(
        "TELEGRAM_EXECUTION",
        "Telegram (or other messenger) handlers",
        (
            _imp(_TG),
            r"api\.telegram\.org",
            r"\bTELEGRAM_\w+",
            r"\b(?:CommandHandler|MessageHandler|CallbackQueryHandler|TelegramClient|send_message_to_telegram)\b",
        ),
    ),
)

MCP_WRITE_PATTERNS = (
    r"\bapp\.db\.writer\b|\bfrom\s+app\.db\s+import\s+writer\b|\bSupabaseDB\b",
    r"\bapp\.services\.(?:pairs\.review|journal)\b",
    r"\.(?:upsert|insert|delete|rpc)\(",
    r"(?i)[\"']readOnlyHint[\"']\s*:\s*false",
)


@dataclass
class Finding:
    rule: str
    file: str
    line: int
    text: str


@dataclass
class AuditReport:
    root: str
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def by_rule(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {r.id: [] for r in RULES}
        out["MCP_WRITE_TOOLS"] = []
        for f in self.findings:
            out.setdefault(f.rule, []).append(f)
        return out

    def titles(self) -> dict[str, str]:
        t = {r.id: r.title for r in RULES}
        t["MCP_WRITE_TOOLS"] = "MCP write tools (registry must be exactly 12 read-only tools)"
        return t

    def render(self) -> str:
        titles = self.titles()
        lines = [f"Compliance audit of {self.root} ({self.files_scanned} files scanned)", ""]
        for rid, fs in self.by_rule().items():
            lines.append(f"[{'FAIL' if fs else 'PASS'}] {rid}: {titles[rid]}")
            lines += [f"       {f.file}:{f.line}: {f.text.strip()[:110]}" for f in fs]
        lines += [
            "",
            "RESULT: "
            + (
                "PASS - no execution or platform-integration code found"
                if self.ok
                else f"FAIL - {len(self.findings)} finding(s)"
            ),
        ]
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "files_scanned": self.files_scanned,
                "findings": [f.__dict__ for f in self.findings],
            },
            indent=2,
        )


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if rel.as_posix() in SELF_EXCLUDED:
            continue
        if (
            p.suffix in CODE_SUFFIXES
            or p.name in CODE_NAMES
            or p.name.startswith("Dockerfile")
            or p.name.startswith(".env")
        ):
            out.append(p)
    return out


def _scan_text(
    rule_id: str, patterns: tuple[str, ...], rel: str, text: str, out: list[Finding]
) -> None:
    compiled = [
        re.compile(p, re.IGNORECASE if rule_id == "TELEGRAM_EXECUTION" else 0) for p in patterns
    ]
    for i, line in enumerate(text.splitlines(), 1):
        if any(c.search(line) for c in compiled):
            out.append(Finding(rule_id, rel, i, line))


def audit(root: Path | str) -> AuditReport:
    root = Path(root).resolve()
    files = _files(root)
    rep = AuditReport(root=str(root), files_scanned=len(files))
    for p in files:
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8", errors="ignore")
        for rule in RULES:
            pats = rule.patterns
            if rule.id == "BROWSER_AUTOMATION" and p.suffix not in {
                ".toml",
                ".txt",
                ".json",
                ".cfg",
                ".ini",
                ".yaml",
                ".yml",
            }:
                pats = tuple(x for x in pats if not x.startswith("(?im)^"))
            if (
                rule.id == "TRADING_CREDENTIALS"
                or p.suffix in {".py", ".ts", ".js", ".sql", ".sh", ".ps1"}
                or p.name.startswith(".env")
                or p.suffix in {".toml", ".txt", ".json", ".yaml", ".yml", ".cfg", ".ini"}
                or "Dockerfile" in p.name
            ):
                _scan_text(rule.id, pats, rel, text, rep.findings)
    # --- MCP write tools ---
    mcp_dir = root / "app" / "mcp"
    if mcp_dir.is_dir():
        reg = mcp_dir / "registry.py"
        if reg.is_file():
            rtext = reg.read_text(encoding="utf-8", errors="ignore")
            names = re.findall(r"ToolSpec\(\s*[\"'](\w+)[\"']", rtext)
            for n in names:
                if n not in ALLOWED_MCP_TOOLS:
                    rep.findings.append(
                        Finding(
                            "MCP_WRITE_TOOLS",
                            "app/mcp/registry.py",
                            0,
                            f"unexpected MCP tool '{n}'",
                        )
                    )
                if re.match(
                    rf"^(?:{_VERBS}|write|update|delete|insert|set|add|remove|record|save|approve|exclude)_",
                    n,
                ):
                    rep.findings.append(
                        Finding(
                            "MCP_WRITE_TOOLS",
                            "app/mcp/registry.py",
                            0,
                            f"write-style MCP tool name '{n}'",
                        )
                    )
            if len(names) != len(set(names)):
                rep.findings.append(
                    Finding("MCP_WRITE_TOOLS", "app/mcp/registry.py", 0, "duplicate tool names")
                )
        for p in sorted(mcp_dir.rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            _scan_text(
                "MCP_WRITE_TOOLS",
                MCP_WRITE_PATTERNS,
                rel,
                p.read_text(encoding="utf-8", errors="ignore"),
                rep.findings,
            )
    return rep
