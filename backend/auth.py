"""Google sign-in and access rules for the API.

require_google_auth is registered as HTTP middleware in main.py. It verifies the
Google ID token, stores the signed-in user on request.state.user, and blocks
view-only (limited) users from the private paths listed below. The helper
functions read request.state.user.

The path lists must use exactly the same URLs as the routers; a renamed URL
that is missing here would be open to view-only users.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from backend.config import (
    ALLOWED_USER_EMAILS,
    AUTHORIZED_USER_EMAILS,
    GOOGLE_AUTH_ENABLED,
    GOOGLE_CLIENT_ID,
)


def current_user_role(request: Request | None) -> str:
    if not request:
        return "full"
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return str(user.get("role") or "full")
    return "full"


def has_full_access(request: Request | None) -> bool:
    return current_user_role(request) == "full"


def current_user_email(request: Request | None) -> str:
    if not request:
        return ""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return str(user.get("email") or "").strip().casefold()
    return ""


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/api/prices/import",
    "/api/prices/intraday/sample",
    "/api/accounts/merge",
}

FULL_ACCESS_ONLY_EXACT_PATHS = {
    "/api/accounts",
    "/api/management",
    "/api/management/allocation",
    "/api/compensation",
    "/api/performance/portfolio",
    "/api/prices/intraday/refresh",
    "/api/portfolio/controls",
    "/api/sheets/tickers/sync",
    "/api/tickers/archive",
    "/api/tickers/restore",
    "/api/tickers/watchlist",
}

FULL_ACCESS_ONLY_PREFIXES = (
    "/api/position/",
    "/api/transactions",
)

FULL_ACCESS_ONLY_WRITE_EXACT_PATHS = {
    "/api/portfolio",
}

FULL_ACCESS_ONLY_WRITE_PREFIXES = (
    "/api/stock/",
)


async def require_google_auth(request: Request, call_next):
    if (
        not GOOGLE_AUTH_ENABLED
        or request.method == "OPTIONS"
        or not request.url.path.startswith("/api")
        or request.url.path in PUBLIC_API_PATHS
    ):
        return await call_next(request)

    if not GOOGLE_CLIENT_ID or not AUTHORIZED_USER_EMAILS:
        return JSONResponse(
            status_code=503,
            content={"detail": "Google authentication is not fully configured"},
        )

    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return JSONResponse(status_code=401, content={"detail": "Sign in required"})

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError:
        return JSONResponse(status_code=401, content={"detail": "Invalid sign-in token"})

    email = str(claims.get("email") or "").strip().casefold()
    if not email or email not in AUTHORIZED_USER_EMAILS:
        return JSONResponse(status_code=403, content={"detail": "Email is not allowed"})

    role = "full" if email in ALLOWED_USER_EMAILS else "limited"
    request.state.user = {
        "email": email,
        "name": claims.get("name") or "",
        "picture": claims.get("picture") or "",
        "role": role,
    }

    write_method = request.method.upper() not in {"GET", "HEAD"}
    if role != "full" and (
        request.url.path in FULL_ACCESS_ONLY_EXACT_PATHS
        or any(request.url.path.startswith(prefix) for prefix in FULL_ACCESS_ONLY_PREFIXES)
        or (
            write_method
            and (
                request.url.path in FULL_ACCESS_ONLY_WRITE_EXACT_PATHS
                or any(
                    request.url.path.startswith(prefix)
                    for prefix in FULL_ACCESS_ONLY_WRITE_PREFIXES
                )
            )
        )
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Private portfolio data is not available for this account"},
        )

    return await call_next(request)
