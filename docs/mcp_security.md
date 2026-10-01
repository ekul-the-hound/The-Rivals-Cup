# MCP security

Scope: the one read-only MCP server in `app/mcp`. It is a **separate FastAPI app** (`app.mcp.server`). It does
not mount the REST API or the admin routes, so nothing that can write is reachable through its URL.

## What it can and cannot do

| Requirement | How it is enforced | Test |
|---|---|---|
| Exactly 12 tools, all read-only | `app/mcp/registry.py`; every tool has `readOnlyHint: true`, `destructiveHint: false`, `additionalProperties: false` | `test_exactly_twelve_read_only_tools` |
| No write to Supabase | Handlers receive a `ReadOnlyStore` that exposes only `select`/`count`; no attribute forwarding; AST scan bans `upsert/insert/delete/rpc/execute` and write SQL | `test_read_only_store_has_no_write_surface`, `test_tools_only_select_and_never_change_data`, `test_mcp_package_has_no_write_execution_telegram_browser_wsr_or_network_access` |
| Cannot change pairs, portfolios, settings, blackouts | Those writers (`app.services.pairs.review`, `app.db.writer`) are never imported; a fresh-interpreter test proves they are not even loaded | `test_fresh_interpreter_loads_no_write_provider_job_or_execution_modules` |
| No orders, WSR, Trader View, brokerage, browser, Telegram | Forbidden imports and URLs fail the build; `scripts/check_no_execution.py` also scans `app/` | compliance tests above |
| No shell, filesystem, arbitrary SQL, arbitrary URL | `os`, `subprocess`, `pathlib`, `httpx`, `requests`, `socket` imports are banned in `app/mcp`; tools take typed parameters only; text filters are plain substring matches | same |
| No secrets or internals in output | Errors are sanitised to a generic message plus `request_id`; validation errors echo field names, never values; provider error text is omitted | `test_internal_errors_are_sanitised`, `test_every_tool_returns_full_envelope` |
| Prompt-injection resistance | News, filing, company and note text is stripped of control/bidi characters, length-bounded, tagged `content_trust: UNTRUSTED`, and the envelope says it is data. The server has no write path, so injected text cannot cause a write | `test_news_text_is_sanitised_and_marked_untrusted`, `test_no_tool_or_resource_writes_even_with_hostile_prompt_text` |
| No order-entry language | Responses are checked for "submit/place/send/queue/execute an order/trade" | `test_responses_never_instruct_order_entry` |
| Every response: `as_of`, freshness, missing/stale warnings, sources, `research_only=true`, manual-decision notice | `app/mcp/envelope.py` | `test_every_tool_returns_full_envelope` |

## Authentication

- **Production:** a Supabase Auth access token (obtained by Claude through OAuth 2.1 with PKCE) for the single
  owner. The server calls Supabase Auth to validate it (cached 60 s) and requires `user.id == OWNER_USER_ID`.
  The data store is then built with the caller's own JWT and the anon key, so Postgres RLS also applies. **No
  service-role key is used in production.** Non-JWT-shaped tokens are rejected without a network call.
- **401 contract:** unauthenticated requests get `401` with
  `WWW-Authenticate: Bearer resource_metadata="https://HOST/.well-known/oauth-protected-resource"`, which is
  what Claude needs to start OAuth. Metadata lists the authorization server.
- **DEVELOPMENT ONLY:** `MCP_DEV_BEARER_TOKEN` (32+ characters). Settings validation refuses it unless
  `APP_ENV=development`, `create_app` refuses to start in production if it is set, and metadata never mentions
  it. In this mode the store uses the service-role key (bypasses RLS), which is why it must stay local.
- Token comparison is constant-time; tokens are never logged or echoed.
- Production startup refuses to run without an `https://` `MCP_PUBLIC_URL`, `SUPABASE_URL`, anon key and
  `OWNER_USER_ID`.

## Transport and abuse controls

- `POST /mcp` only (stateless JSON responses). `GET`, `DELETE`, `PUT`, `PATCH` return 405. No sessions, no SSE.
- `Origin` is checked against `MCP_ALLOWED_ORIGINS` (default claude.ai, claude.com) to block DNS-rebinding and
  cross-site calls. Requests without `Origin` (non-browser clients) are allowed but still need a token.
- Body limit 64 KB, JSON content type required, batch limit 10, `MCP-Protocol-Version` validated.
- Rate limit per principal: `MCP_RATE_LIMIT_PER_MINUTE` units/minute (default 60). Cheap tools cost 1, heavier
  ones 2-3. Failed-auth attempts are limited to 20/minute per client address. The limiter is in-process; behind
  several instances add an edge limit.
- Response limit: `MCP_MAX_RESPONSE_BYTES` (default 80,000). Lists are halved until the payload fits and the
  response says `truncated: true`. List tools paginate with opaque cursors; `limit` is capped at 50.
- Every response has a `request_id` (also the `X-Request-ID` header) for log correlation.
- OpenAPI and docs UIs are disabled.

## Audit logging

Each call writes one JSON line to the `mcp.audit` logger (stdout/stderr): time, `request_id`, principal id,
tool, a SHA-256 hash of the arguments, status, duration and size. It never contains tokens, argument values or
response content. The logs go to your hosting platform's log store.

It deliberately does **not** write to Supabase. The MCP must not write to the database, and a database audit
sink would be a write path reachable from request handling. If you later want a database audit trail, ship
the log lines to a separate collector or do it with a platform log drain, not from this process.

## Residual risks and limits

- The 1%-of-ADV liquidity cap, exposure and score values are estimates; Claude can misreport them. Trader View
  and the official rules control.
- Data quality: Yahoo prices are unofficial, news is third-party, and event blackouts are manual. Missing or
  stale data is reported in every response but a person must still check.
- Tokens are bearer tokens. If a Supabase access token is stolen it is valid until it expires; keep expiry short.
- Supabase access tokens are not audience-bound to this server (no RFC 8707 resource binding). Anyone who
  holds an owner token for your project could use it here; this server only accepts that one user.
- The user's own JWT could in principle write through RLS if code tried to; the protection is that this code
  has no write call and tests enforce it. For extra safety, review RLS policies so that tables read by the MCP
  are not needed for writes by the same role, or use a read-only database role behind a separate API.
- Rate limiting is per process.
- Unverified: behavior against live Claude Web and live Supabase OAuth. Test with a throwaway session first.
- Claude connectors run with whatever the user approves; use the Blocked setting for tools you do not need.

## Reporting a problem

If a response ever tells you to place or manage a trade, or you see the server write anything, stop using the
connector (Customize > Connectors > Remove), then check the audit log by `request_id`.
