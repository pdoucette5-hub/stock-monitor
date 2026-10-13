"""Who is making the request, and what they are allowed to see.

The Google sign-in middleware in main.py stores the signed-in user on
request.state.user; these helpers read it."""

from __future__ import annotations

from fastapi import Request


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
