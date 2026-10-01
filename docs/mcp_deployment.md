# Deploying the MCP server

Pick one. **Option A (FastAPI) is implemented and tested.** Option B (Supabase Edge Functions) is documented
with an honest compatibility verdict.

## Verdict: Edge Functions or FastAPI?

Supabase supports remote MCP servers on Edge Functions (TypeScript, Streamable HTTP, stateless, with
user-scoped auth and RLS; see https://supabase.com/docs/guides/ai-tools/byo-mcp). That is a good fit for
simple "query the database" tools. It is **not a drop-in fit here**: the pair engine, scoring, factor and
blackout logic are Python/pandas, and the tools are built on them. Running them in Edge Functions means
rewriting that logic in TypeScript and keeping two copies correct. So the implemented path is a small FastAPI
service that reads from Supabase. Option B below describes a hybrid you can build later.

## Option A: FastAPI service (implemented)

`app.mcp.server` is a standalone ASGI app with one MCP endpoint at `/mcp`, plus
`/.well-known/oauth-protected-resource[/mcp]` and `/healthz`. It does not include the REST/admin routers.

### 1. Environment (production)

```
APP_ENV=production
MCP_PUBLIC_URL=https://mcp.your-domain.example/mcp      # exact URL you give Claude
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=<anon key>                            # public key; RLS applies
OWNER_USER_ID=<your Supabase auth user uuid>
MCP_ALLOWED_ORIGINS=https://claude.ai,https://claude.com
WSR_EST_ADV_PCT_CAP=0.01
```

Do not set `MCP_DEV_BEARER_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY`, `DEV_BEARER_TOKEN`, or any provider key on this
service. The server refuses to start in production if the dev token is present or if the HTTPS URL, owner id or
Supabase settings are missing.

### 2. Run

```powershell
docker build -f Dockerfile.mcp -t rival-mcp .
docker run -p 8080:8080 --env-file .env.mcp rival-mcp
# or without Docker
pip install .
uvicorn app.mcp.server:get_app --factory --host 0.0.0.0 --port 8080 --proxy-headers
```

### 3. Public HTTPS

Put it behind a platform or reverse proxy that terminates TLS (for example Cloud Run, Fly.io, Render, or Caddy/
nginx on a VPS) with a real certificate on your domain. Requirements for Claude:

- Reachable from the public internet (Anthropic egress `160.79.104.0/21`); no IP allowlist other than that.
- Responses within a few seconds. `/mcp` calls read the database only; set a request timeout of at least 60 s.
- Route both `/mcp` and `/.well-known/*` on the same host.
- Run a single instance first (the rate limiter is per process); scale up with an edge rate limit.
- Send logs to a store you can search by `request_id`.

Verify with the three `curl` checks in `docs/mcp_claude_web_setup.md`.

### 4. Authentication (OAuth through Supabase Auth)

Claude Web needs an OAuth 2.0 authorization server. Supabase Auth can be that server. Summary of what
Supabase and Claude require (check Supabase's current docs, since this feature is evolving):

1. Enable Supabase's OAuth 2.1 server for the project and use **asymmetric JWT signing** (not legacy HS256).
2. Enable **dynamic client registration** (or support Client ID Metadata Documents) so Claude can register.
3. Allow redirect URI `https://claude.ai/api/mcp/auth_callback`.
4. Host an authorization/consent page. Supabase does not provide a hosted consent screen for custom OAuth
   clients; you must build a small page (a few lines with `supabase-js`) that signs you in and approves the
   request, and set its URL in the Supabase dashboard. **This repository does not include that page.**
5. Set `MCP_OAUTH_ISSUER` if the issuer differs from `{SUPABASE_URL}/auth/v1`, and confirm its discovery
   document is public: `curl {issuer}/.well-known/oauth-authorization-server` (or OpenID configuration).
6. Put your own user id in `OWNER_USER_ID`. Only that user is accepted.

Claude's requirements for the authorization server and token endpoint are at
https://claude.com/docs/connectors/building/authentication (401 + `resource_metadata`, PKCE S256, form-encoded
token endpoint, refresh-token rotation, 10 s discovery latency).

If you cannot run an OAuth server yet, use the server only locally with the development token and the test
client. Do not expose the development token publicly. (Claude's static-header authentication is in limited beta;
if your organization has it, that is a separate choice with a shared credential, and this repo does not enable
it in production.)

### 5. Connect Claude

Follow `docs/mcp_claude_web_setup.md`.

## Option B: Supabase Edge Functions (not implemented here)

Compatible in principle; requires a TypeScript port. A realistic hybrid:

1. Keep Python for compute. The weekly job (`python -m scripts.build_weekly_portfolio --persist`) already stores
   per-pair packets (`pair_rankings.packet`) and the weekly draft (`weekly_portfolios`).
2. Write one Edge Function `mcp` using the MCP TypeScript SDK (or `mcp-lite`) over Streamable HTTP, building a
   fresh server per request, with Supabase Auth and `withSupabase({ auth: 'user' })` so reads go through RLS.
3. Implement the 12 tools as typed SELECT queries over stored tables and stored packets (no pandas). Tools that
   need live computation (`get_liquidity_check` from bars, `rank_sector_etfs`) would be reimplemented in SQL/TS.
4. Deploy: link the project, push auth config, `supabase functions deploy mcp --no-verify-jwt` only if the function
   verifies OAuth tokens itself. The function URL is the public MCP endpoint.
5. Keep the same envelope, schemas, limits and read-only guarantees, and port the compliance tests.

Trade-offs: no servers to run and Supabase Auth integration is first-party, but duplicated logic, packets only as
fresh as the last weekly build, and different test tooling. Choose it only if you want zero hosting.
