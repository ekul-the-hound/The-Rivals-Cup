"""Authentication for the MCP endpoint.

PRODUCTION: a Supabase Auth access token (issued through Supabase's OAuth 2.1 server, so Claude Web can
use the standard OAuth flow) belonging to the single owner. The data store then uses that user's JWT, so
Row Level Security applies on top of this check.

DEVELOPMENT ONLY: a static bearer token (MCP_DEV_BEARER_TOKEN). It is refused unless APP_ENV=development
and is never advertised in production metadata.

No service-role key is used in production. Nothing here ever logs or returns a token.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import Settings


class AuthError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code


@dataclass(frozen=True)
class Principal:
    id: str
    mode: str  # "supabase" | "development"
    token: str = ""


def parse_bearer(header: str | None) -> str:
    if not header:
        raise AuthError(401, "missing_token")
    scheme, _, tok = header.partition(" ")
    if scheme.lower() != "bearer" or not tok.strip() or len(tok) > 4096:
        raise AuthError(401, "invalid_token")
    return tok.strip()


class Authenticator:
    """`verify_jwt(token) -> user_id` is injectable for tests; default calls Supabase Auth."""

    def __init__(self, settings: Settings, verify_jwt: Callable[[str], str] | None = None, cache_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        self.s = settings
        self._verify = verify_jwt or self._verify_with_supabase
        self._cache: dict[str, tuple[float, str]] = {}
        self._ttl, self._clock = cache_seconds, clock

    def _verify_with_supabase(self, token: str) -> str:
        from app.db.store import create_supabase_client

        if token.count(".") != 2:  # not JWT-shaped: reject without a network call
            raise AuthError(401, "invalid_token")
        try:
            client = create_supabase_client(
                self.s.supabase_url, self.s.supabase_anon_key.get_secret_value()
            )
            user = client.auth.get_user(token).user
        except Exception as exc:  # never leak provider/internal errors
            raise AuthError(401, "invalid_token") from exc
        if user is None:
            raise AuthError(401, "invalid_token")
        return str(user.id)

    def authenticate(self, header: str | None) -> Principal:
        token = parse_bearer(header)
        if self.s.mcp_dev_token_enabled:
            expected = self.s.mcp_dev_bearer_token.get_secret_value()  # type: ignore[union-attr]
            if hmac.compare_digest(token.encode(), expected.encode()):
                return Principal("dev", "development", token)
        if not self.s.owner_user_id:
            raise AuthError(503, "server_not_configured")
        key = hashlib.sha256(token.encode()).hexdigest()
        hit = self._cache.get(key)
        now = self._clock()
        if hit and now - hit[0] < self._ttl:
            uid = hit[1]
        else:
            uid = self._verify(token)
            self._cache[key] = (now, uid)
            if len(self._cache) > 500:
                self._cache.clear()
        if not hmac.compare_digest(uid.encode(), self.s.owner_user_id.encode()):
            raise AuthError(403, "not_owner")
        return Principal(uid, "supabase", token)


def store_for(principal: Principal, settings: Settings) -> Any:
    """Build the read-only data source. Production: the caller's own JWT (RLS applies)."""
    from app.db.store import SupabaseStore, create_supabase_client

    if principal.mode == "development":
        key = settings.supabase_service_role_key.get_secret_value()
        if not (settings.supabase_url and key):
            raise AuthError(503, "server_not_configured")
        return SupabaseStore(
            create_supabase_client(settings.supabase_url, key)
        )  # DEV ONLY (bypasses RLS)
    return SupabaseStore(create_supabase_client(settings.supabase_url, settings.supabase_anon_key.get_secret_value(), principal.token))  # fmt: skip
