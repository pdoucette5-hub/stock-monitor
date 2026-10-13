"""Health check and sign-in configuration endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend.config import GOOGLE_AUTH_ENABLED, GOOGLE_CLIENT_ID


router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/auth/config")
def get_auth_config() -> dict[str, Any]:
    return {
        "enabled": GOOGLE_AUTH_ENABLED,
        "client_id": GOOGLE_CLIENT_ID if GOOGLE_AUTH_ENABLED else "",
    }


@router.get("/api/auth/me")
def get_auth_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        return {"authenticated": False, "role": "full"}
    return {
        "authenticated": True,
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "picture": user.get("picture") or "",
        "role": user.get("role") or "full",
    }
