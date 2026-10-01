"""Authentication + store construction.

Production: Supabase Auth JWT, owner-only. The Store uses the caller's JWT, so Row Level Security
is enforced by Postgres in addition to the owner check here.

DEVELOPMENT ONLY: a static DEV_BEARER_TOKEN may be used (APP_ENV=development). It maps to a
service-role store that BYPASSES RLS. It is refused at startup in production.
"""

import hmac
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.db.store import Store, SupabaseStore, create_supabase_client
from app.db.writer import DB, SupabaseDB

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


def build_service_store(settings: Settings) -> Store:
    return SupabaseStore(
        create_supabase_client(
            settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
        )
    )


def build_user_store(settings: Settings, jwt: str) -> Store:
    return SupabaseStore(
        create_supabase_client(
            settings.supabase_url, settings.supabase_anon_key.get_secret_value(), jwt
        )
    )


def verify_owner_jwt(settings: Settings, jwt: str) -> str:
    """Return the user id if the JWT is valid and belongs to the single owner."""
    client = create_supabase_client(
        settings.supabase_url, settings.supabase_anon_key.get_secret_value()
    )
    try:
        user = client.auth.get_user(jwt).user
    except Exception as exc:  # invalid/expired token
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
    if user is None or str(user.id) != settings.owner_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not the owner")
    return str(user.id)


def get_store(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Store:
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = creds.credentials
    if settings.dev_token_enabled:
        expected = settings.dev_bearer_token.get_secret_value()  # type: ignore[union-attr]
        if hmac.compare_digest(token.encode(), expected.encode()):
            log.warning("DEV bearer token in use (development only; bypasses RLS)")
            return build_service_store(settings)
    if not settings.owner_user_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OWNER_USER_ID not configured")
    verify_owner_jwt(settings, token)
    return build_user_store(settings, token)


def build_service_db(settings: Settings) -> DB:
    return SupabaseDB(
        create_supabase_client(
            settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
        )
    )


def get_admin_db(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> DB:
    """ADMIN ONLY (owner JWT, or the development-only dev token). Returns a service-role DB used
    solely by data-refresh jobs. Not reachable by the future MCP endpoint."""
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = creds.credentials
    if settings.dev_token_enabled:
        expected = settings.dev_bearer_token.get_secret_value()  # type: ignore[union-attr]
        if hmac.compare_digest(token.encode(), expected.encode()):
            return build_service_db(settings)
    if not settings.owner_user_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OWNER_USER_ID not configured")
    verify_owner_jwt(settings, token)
    return build_service_db(settings)
