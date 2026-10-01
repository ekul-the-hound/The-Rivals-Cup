"""ONE read-only MCP server (Streamable HTTP). Research data in, nothing out.

No module in this package may import a write path, a job runner, a data provider, a scheduler,
a messenger, a browser driver, or anything that can reach WSR / Trader View / a broker. This is
enforced by tests (static scan + a fresh-interpreter import check).
"""

SERVER_NAME = "rival-research"
SERVER_TITLE = "Rival Research (read-only)"
SERVER_VERSION = "1.0.0"
RULES_VERSION = "2026-10-01.1"
