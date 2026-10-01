"""The ONE MCP server: a standalone FastAPI app exposing a single MCP endpoint.

Routes:  POST /mcp (Streamable HTTP, JSON responses) · GET /.well-known/oauth-protected-resource[/mcp]
         GET /healthz.   GET/DELETE /mcp -> 405 (stateless: no SSE stream, no sessions).
Run:     uvicorn app.mcp.server:get_app --factory --host 0.0.0.0 --port 8080   (behind HTTPS in production)

This app deliberately does NOT include the REST/admin routers, so nothing that can write is reachable.
"""

import json
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.config import Settings, get_settings
from app.mcp import SERVER_VERSION
from app.mcp.audit import audit
from app.mcp.auth import Authenticator, AuthError, Principal, store_for
from app.mcp.protocol import RATE_LIMITED, SUPPORTED_VERSIONS, Env, handle_message
from app.mcp.ratelimit import RateLimiter

MAX_BODY_BYTES = 64 * 1024
MAX_BATCH = 10
DEV_ORIGINS = ("http://localhost:6274", "http://127.0.0.1:6274", "http://localhost:3000", "http://127.0.0.1:3000")  # fmt: skip
SECURITY_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}  # fmt: skip


def validate_production_settings(s: Settings) -> None:
    """Refuse to start in production without HTTPS and owner-only auth configured."""
    if s.app_env != "production":
        return
    problems = []
    if not s.mcp_public_url.startswith("https://"):
        problems.append("MCP_PUBLIC_URL must be an https:// URL")
    for name, val in (("SUPABASE_URL", s.supabase_url), ("OWNER_USER_ID", s.owner_user_id), ("SUPABASE_ANON_KEY", s.supabase_anon_key.get_secret_value())):  # fmt: skip
        if not val:
            problems.append(f"{name} is required")
    if s.mcp_dev_token_enabled:
        problems.append("MCP_DEV_BEARER_TOKEN must not be set in production")
    if problems:
        raise RuntimeError("MCP server refused to start: " + "; ".join(problems))


def create_app(
    settings: Settings | None = None,
    *,
    store_provider: Callable[[Principal], Any] | None = None,
    verify_jwt: Callable[[str], str] | None = None,
    clock: Callable[[], Any] | None = None,
) -> FastAPI:
    s = settings or get_settings()
    validate_production_settings(s)
    authn = Authenticator(s, verify_jwt)
    limiter = RateLimiter(s.mcp_rate_limit_per_minute)
    fail_limiter = RateLimiter(20)  # failed auth per client address per minute
    provider = store_provider or (lambda p: store_for(p, s))
    allowed = {o.strip().rstrip("/") for o in s.mcp_allowed_origins.split(",") if o.strip()}
    if s.app_env == "development":
        allowed |= set(DEV_ORIGINS)
    app = FastAPI(title="Rival Research MCP (read-only)", version=SERVER_VERSION, docs_url=None, redoc_url=None, openapi_url=None)  # fmt: skip

    def public_url(request: Request) -> str:
        return s.mcp_public_url or str(request.base_url).rstrip("/") + "/mcp"

    def metadata_url(request: Request) -> str:
        u = urlsplit(public_url(request))
        return f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource"

    def reply(status: int, body: Any, rid: str, headers: dict[str, str] | None = None) -> Response:
        h = {**SECURITY_HEADERS, "X-Request-ID": rid, **(headers or {})}
        if body is None:
            return Response(status_code=status, headers=h)
        return JSONResponse(body, status_code=status, headers=h)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "rival-research-mcp"}

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    def protected_resource(request: Request) -> Response:
        rid = str(uuid.uuid4())
        issuer = s.mcp_oauth_issuer or (
            f"{s.supabase_url.rstrip('/')}/auth/v1" if s.supabase_url else ""
        )
        body: dict[str, Any] = {"resource": public_url(request), "bearer_methods_supported": ["header"], "resource_name": "Rival Research (read-only)"}  # fmt: skip
        if issuer:
            body["authorization_servers"] = [issuer]
        return reply(200, body, rid)

    @app.api_route("/mcp", methods=["GET", "DELETE", "PUT", "PATCH"])
    def not_allowed(request: Request) -> Response:
        return reply(405, {"error": "method_not_allowed"}, str(uuid.uuid4()), {"Allow": "POST"})

    @app.post("/mcp")
    async def mcp_endpoint(request: Request) -> Response:
        rid = str(uuid.uuid4())
        client = request.client.host if request.client else "unknown"
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in allowed:
            audit(request_id=rid, kind="reject", reason="origin")
            return reply(403, {"error": "origin_not_allowed"}, rid)
        # --- auth (before reading the body) ---
        try:
            principal = authn.authenticate(request.headers.get("authorization"))
        except AuthError as exc:
            ok, retry = fail_limiter.check(client)
            audit(request_id=rid, kind="auth_failed", code=exc.code, client=client)
            if not ok:
                return reply(429, {"error": "too_many_attempts"}, rid, {"Retry-After": str(retry)})
            hdr = {}
            if exc.status == 401:
                hdr["WWW-Authenticate"] = f'Bearer resource_metadata="{metadata_url(request)}"'
                if exc.code == "invalid_token":
                    hdr["WWW-Authenticate"] += ', error="invalid_token"'
            return reply(exc.status, {"error": exc.code}, rid, hdr)
        ver = request.headers.get("mcp-protocol-version")
        if ver and ver not in SUPPORTED_VERSIONS:
            return reply(
                400,
                {"error": "unsupported_protocol_version", "supported": list(SUPPORTED_VERSIONS)},
                rid,
            )
        if "application/json" not in request.headers.get("content-type", ""):
            return reply(415, {"error": "content_type_must_be_application_json"}, rid)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return reply(413, {"error": "request_too_large"}, rid)
        try:
            payload = json.loads(raw)
        except ValueError:
            return reply(
                400,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
                rid,
            )
        try:
            store = provider(principal)
        except AuthError as exc:
            return reply(exc.status, {"error": exc.code}, rid)
        env = Env(store=store, settings=s, request_id=rid, principal=principal.id, charge=lambda cost: limiter.check(principal.id, cost))  # fmt: skip
        if clock:
            env.clock = clock
        batch = payload if isinstance(payload, list) else [payload]
        if not batch or len(batch) > MAX_BATCH:
            return reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid batch size"}}, rid)  # fmt: skip
        out = [r for r in (handle_message(m, env) for m in batch) if r is not None]
        if not out:
            return reply(202, None, rid)
        limited = [r for r in out if r.get("error", {}).get("code") == RATE_LIMITED]
        if limited and len(out) == 1:
            retry = str(limited[0]["error"].get("data", {}).get("retry_after_seconds", 1))
            return reply(429, out[0], rid, {"Retry-After": retry})
        return reply(200, out if isinstance(payload, list) else out[0], rid)

    return app


def get_app() -> FastAPI:
    """ASGI factory: `uvicorn app.mcp.server:get_app --factory --host 0.0.0.0 --port 8080`."""
    return create_app()
