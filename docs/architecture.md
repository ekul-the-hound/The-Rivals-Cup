# Architecture and the MCP decision

## Layers
`providers` (fetch) -> `ingestion` (normalize, write via service role) -> `features` (returns,
correlation, liquidity) -> `pairs` (rank) -> `packets` (research packet) -> read paths
(`api` status endpoints now; the MCP endpoint later). Only the read paths exist today.

## One MCP endpoint (decision, to validate in the MCP phase)
- Exactly **one** remote MCP server, Streamable HTTP over HTTPS, read-only tools.
- Preferred: Supabase Edge Function (`supabase/functions/mcp/`), stateless Streamable HTTP, querying
  through the `mcp_readonly` role, with OAuth-compatible auth (Supabase Auth as the authorization server).
- Fallback: a small FastAPI service exposing the same single public MCP endpoint, if Edge Function limits
  or Claude Web connector OAuth requirements (dynamic client registration, discovery metadata) don't fit.
- I have not yet tested either against Claude Web custom connectors; that check is the first MCP task.
- Local dev only: a bearer-token mode, clearly marked development-only.
- Tool calls are logged to `mcp_audit_logs` by the server as an access log (args redacted by
  `app.services.audit.redact_args`). That is server-side logging, not a tool mutation; tools themselves
  have no write path and the DB role has no write grants.
