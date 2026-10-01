"""Local MCP test client.

  python -m scripts.mcp_test_client --mock                       # in-process: mock data, all 12 tools + 4 resources
  python -m scripts.mcp_test_client --mock --tool get_liquidity_check --args '{"ticker":"KO","intended_notional":10000}'
  python -m scripts.mcp_test_client --url http://127.0.0.1:8080/mcp --token $env:MCP_DEV_BEARER_TOKEN   # a running server

Read-only: it only calls the server's read-only tools. DEVELOPMENT ONLY token handling: never paste a real token
into shared logs; production uses OAuth through Claude Web.
"""

import argparse
import json
import sys
from typing import Any

import httpx

SAMPLE_ARGS: dict[str, dict[str, Any]] = {
    "get_competition_rules_summary": {},
    "get_market_dashboard": {},
    "rank_sector_etfs": {"lookbacks": [1, 5, 20]},
    "get_peer_pair_candidates": {"limit": 5},
    "get_peer_pair_packet": {"long_ticker": "KO", "short_ticker": "PEP"},
    "get_stock_research_packet": {"ticker": "KO"},
    "search_sec_filings": {"ticker": "KO", "filing_types": ["8-K", "10-Q", "4"], "start_date": "2026-08-01", "end_date": "2026-10-01"},
    "search_news": {"ticker": "KO"},
    "get_liquidity_check": {"ticker": "KO", "intended_notional": 10000},
    "get_portfolio_risk_context": {},
    "get_weekly_event_blackout_list": {"week_start": "2026-09-28", "week_end": "2026-10-02"},
    "get_manual_entry_checklist": {"long_ticker": "KO", "short_ticker": "PEP", "intended_long_notional": 10000, "intended_short_notional": 10000},
}  # fmt: skip
RESOURCES = [
    "rules://rival-cup/current",
    "market://dashboard/current",
    "portfolio://manual/current",
    "quality://current",
]


class Client:
    def __init__(self, post, headers: dict[str, str]) -> None:
        self._post, self._h, self._i = post, headers, 0

    def rpc(self, method: str, params: dict | None = None) -> dict[str, Any]:
        self._i += 1
        r = self._post(
            json={"jsonrpc": "2.0", "id": self._i, "method": method, "params": params or {}},
            headers=self._h,
        )
        if r.status_code >= 400 and not r.content.startswith(b"{"):
            raise SystemExit(f"HTTP {r.status_code}")
        body = r.json()
        if r.status_code in (401, 403, 429):
            raise SystemExit(
                f"HTTP {r.status_code}: {body} {r.headers.get('www-authenticate', '')}"
            )
        return body

    def tool(self, name: str, args: dict | None = None) -> dict[str, Any]:
        res = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        if "error" in res:
            return {"rpc_error": res["error"]}
        return json.loads(res["result"]["content"][0]["text"])


def summarize(name: str, env: dict[str, Any]) -> str:
    if "rpc_error" in env:
        return f"  {name}: RPC ERROR {env['rpc_error']}"
    if env.get("error"):
        return f"  {name}: tool error: {env['error']}"
    keys = ", ".join(list(env["data"])[:4])
    return f"  {name}: ok  research_only={env['research_only']} missing/stale={len(env['missing_or_stale'])} warnings={len(env['warnings'])} keys=[{keys}]"  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mock", action="store_true", help="run an in-process server on offline mock data"
    )
    ap.add_argument("--url", help="MCP URL of a running server, e.g. http://127.0.0.1:8080/mcp")
    ap.add_argument("--token", default="", help="bearer token (DEVELOPMENT ONLY)")
    ap.add_argument("--tool", help="call just this tool")
    ap.add_argument("--args", default="{}", help="JSON arguments for --tool")
    ap.add_argument("--full", action="store_true", help="print full JSON for --tool")
    a = ap.parse_args(argv)
    if a.mock == bool(a.url):
        ap.error("choose exactly one of --mock or --url")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if a.mock:
        from fastapi.testclient import TestClient

        from app.config import Settings
        from app.mcp.server import create_app
        from app.tests.mock_world import fresh_db

        tok = "mock-dev-token-" + "m" * 32
        db = fresh_db()
        c = TestClient(create_app(Settings(app_env="development", mcp_dev_bearer_token=tok, owner_user_id="mock"), store_provider=lambda p: db))  # fmt: skip
        headers["Authorization"] = f"Bearer {tok}"
        cl = Client(lambda **kw: c.post("/mcp", **kw), headers)
    else:
        if a.token:
            headers["Authorization"] = f"Bearer {a.token}"
        http = httpx.Client(timeout=60)
        cl = Client(lambda **kw: http.post(a.url, **kw), headers)
    init = cl.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "local-test-client", "version": "1"}})["result"]  # fmt: skip
    print(
        f"server: {init['serverInfo']['name']} {init['serverInfo']['version']}  protocol {init['protocolVersion']}"
    )
    if a.tool:
        env = cl.tool(a.tool, json.loads(a.args))
        print(json.dumps(env, indent=2) if a.full else summarize(a.tool, env))
        return 0
    tools = cl.rpc("tools/list")["result"]["tools"]
    print(f"{len(tools)} tools: " + ", ".join(t["name"] for t in tools))
    bad = 0
    for t in tools:
        env = cl.tool(t["name"], SAMPLE_ARGS.get(t["name"], {}))
        print(summarize(t["name"], env))
        bad += bool(env.get("error") or env.get("rpc_error"))
    for uri in RESOURCES:
        r = cl.rpc("resources/read", {"uri": uri})
        print(f"  {uri}: " + ("ok" if "result" in r else f"ERROR {r['error']}"))
        bad += "error" in r
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
