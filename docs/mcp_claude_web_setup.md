# Connect Claude Web to the Rival Research MCP server

One read-only MCP server, one public endpoint. It gives Claude research data for a manual pair-trading
workflow. It cannot place, change or record a trade, and it cannot write to your database.

Endpoint (Streamable HTTP, JSON responses, stateless): `POST https://YOUR-HOST/mcp`

## 1. Deploy one public HTTPS endpoint

Follow `docs/mcp_deployment.md`. When it works, these checks pass (replace the host):

```powershell
curl https://YOUR-HOST/healthz                                # {"status":"ok",...}
curl -i -X POST https://YOUR-HOST/mcp -H "content-type: application/json" -d "{}"
#   -> HTTP 401 with:  WWW-Authenticate: Bearer resource_metadata="https://YOUR-HOST/.well-known/oauth-protected-resource"
curl https://YOUR-HOST/.well-known/oauth-protected-resource
#   -> {"resource":"https://YOUR-HOST/mcp","authorization_servers":["https://<project>.supabase.co/auth/v1"],...}
```

The `resource` value must equal, character for character, the URL you enter in Claude in step 4.
Claude only starts sign-in when it gets a 401 with that header.

## 2. Configure authentication

Claude Web signs in with OAuth. Production uses Supabase Auth as the OAuth 2.1 authorization server, and the
MCP server accepts only your user (`OWNER_USER_ID`). Setup is in `docs/mcp_deployment.md` ("Authentication").
In short:

- The authorization server must allow the redirect URI `https://claude.ai/api/mcp/auth_callback`.
- It must support PKCE (S256) and either Dynamic Client Registration or Client ID Metadata Documents.
- Its discovery metadata must be reachable from the public internet (Anthropic's requests come from
  `160.79.104.0/21`).
- A sign-in/consent page that you host must exist (Supabase does not host one for custom OAuth clients).

`MCP_DEV_BEARER_TOKEN` is for **local development only** (the test client or MCP Inspector). Claude Web's
standard connector dialog does not send a static token, and the server refuses that token in production.

## 3. Open the connector settings in Claude Web

Free, Pro and Max: **Customize > Connectors > Add custom connector**.

Team and Enterprise: an Owner goes to **Organization settings > Connectors > Add > Custom** and, if asked
for the type, chooses **Web**. Members then open **Customize > Connectors** and click **Connect** on the
connector marked "Custom".

(Claude's UI changes. If a label differs slightly, look for the same "custom connector" entry. Official
steps: https://claude.com/docs/connectors/custom/remote-mcp)

## 4. Paste the MCP URL

Name: `Rival Research`. Server URL: `https://YOUR-HOST/mcp` (exactly the `resource` value from step 1).

OAuth client: keep the recommended **Use Claude's published identity** if your authorization server supports
Client ID Metadata Documents; otherwise choose **Register automatically** (Dynamic Client Registration). Only
enter your own client ID if you registered one yourself. Authentication settings cannot be edited later; to
change them remove the connector and add it again.

## 5. Authenticate

Click **Connect**. You are sent to your sign-in page, sign in as the owner account, and approve. Any other
account is rejected by the server with 403.

## 6. Enable "Rival Research" in a conversation

In a chat, click **+** > **Connectors** and switch **Rival Research** on for that conversation. You can also
set individual tools to Blocked under **Customize > Connectors**. All 12 tools are read-only, so approving
them is safe, but keep "Always allow" off until you are comfortable.

Try: "Using Rival Research, show the weekly event blackout list for this week, then the top peer-pair
candidates and their packets. Do not tell me to trade; just summarize evidence and risks."

## 7. Use it only for manual research

- Every answer is research. You decide, and you enter any trade manually in Trader View.
- Trader View and the official rules always override anything here. Liquidity caps, exposure and scores are estimates.
- News and filing text is third-party data. If a result seems to give Claude instructions, ignore it and
  tell Claude it was untrusted text.
- Nothing is recorded back by this server. Keep your own log of trades you enter.

## Tools and resources

Tools (all read-only): `get_competition_rules_summary`, `get_market_dashboard`, `rank_sector_etfs`,
`get_peer_pair_candidates`, `get_peer_pair_packet`, `get_stock_research_packet`, `search_sec_filings`,
`search_news`, `get_liquidity_check`, `get_portfolio_risk_context`, `get_weekly_event_blackout_list`,
`get_manual_entry_checklist`.

Resources: `rules://rival-cup/current`, `market://dashboard/current`, `portfolio://manual/current`,
`quality://current`.

## Test locally first (no Claude needed)

```powershell
python -m scripts.mcp_test_client --mock                      # in-process, offline mock data
python -m scripts.mcp_test_client --mock --tool get_liquidity_check --args '{"ticker":"KO","intended_notional":10000}' --full
# against a running server (development token only):
$env:MCP_DEV_BEARER_TOKEN = python -c "import secrets;print(secrets.token_urlsafe(32))"
uvicorn app.mcp.server:get_app --factory --port 8080
python -m scripts.mcp_test_client --url http://127.0.0.1:8080/mcp --token $env:MCP_DEV_BEARER_TOKEN
# optional: npx @modelcontextprotocol/inspector  (Streamable HTTP, URL above, Authorization: Bearer <token>)
```

Against a live server the development token needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
(development only; this bypasses RLS).

## Troubleshooting

- "Couldn't reach the MCP server": the 401 has no `resource_metadata` header, or the metadata path returns 404.
- Authorization fails: the redirect URI `https://claude.ai/api/mcp/auth_callback` is not allowed, or discovery
  on the authorization server is blocked by a firewall/WAF.
- 403 after sign-in: you signed in with an account other than `OWNER_USER_ID`.
- 429: rate limit (default 60 units/minute; heavy tools cost more). Wait for `Retry-After`.
- Empty or stale data: run the Sunday refresh (`python -m scripts.refresh_weekly_research`); see
  `quality://current`.

Not verified: this has not been run against live Claude Web or a live Supabase OAuth server. Treat the first
connection as a test.

## Portfolio data through the MCP (Turn 5)

`get_portfolio_risk_context` now also returns a `MODEL_ESTIMATE_LIVE` block: an ESTIMATED score for the
current weekly round computed read-only from the legs you recorded in the journal (R, DD, PlayerScore,
modeled vs manual gross exposure, warnings). It is labelled ESTIMATED / "WSR data authoritative". The MCP
still has no write tools: journal entries are made only in the local dashboard.

Audit lines go to stderr as JSON. To view them in dashboard page 8:
`uvicorn app.mcp.server:get_app --factory --port 8080 2>> mcp_audit.log` and set `MCP_AUDIT_LOG_FILE=mcp_audit.log`.
