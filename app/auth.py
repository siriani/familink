"""HTTP Basic Auth middleware for the whole app, except /health (Uptime
Kuma and similar pollers need to reach it without credentials).

If ADMIN_PASSWORD is unset, auth is not enforced — familink logs a loud
warning at startup instead of silently running open. This matters more
than it used to: since the group -> MikroTik enforcement feature shipped,
an unauthenticated visitor on the LAN can change what a device's internet
access actually is, not just relabel it in a database.
"""
from __future__ import annotations

import base64
import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import ADMIN_PASSWORD, ADMIN_USER

logger = logging.getLogger("familink.auth")

_EXEMPT_PATHS = {"/health", "/captive"}
_EXEMPT_PREFIXES = ("/static/",)


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ADMIN_PASSWORD or _is_exempt(request.url.path):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode()
                user, _, password = decoded.partition(":")
            except Exception:
                user, password = "", ""
            if secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(
                password, ADMIN_PASSWORD
            ):
                return await call_next(request)

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="familink"'},
            content="Authentication required",
        )


def warn_if_auth_disabled() -> None:
    if not ADMIN_PASSWORD:
        logger.warning(
            "ADMIN_PASSWORD is not set — the admin panel is running WITHOUT "
            "authentication. Anyone who can reach this app can change what "
            "devices are enforced through the MikroTik hotspot. Set "
            "ADMIN_USER/ADMIN_PASSWORD in .env before exposing this beyond "
            "a fully trusted network."
        )
