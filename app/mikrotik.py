"""Async MikroTik RouterOS REST API client.

Ported from the existing hotspot-admin/server.py's `mk()` helper (Basic
Auth, `{base}/rest/{path}`, JSON body, short timeout, normalize errors into
a (status, body) tuple), just made httpx-async to match this project's
FastAPI style.

FOUNDATION PHASE NOTE: only `.get()` is exercised anywhere in this
codebase (see app/sync.py). `.post()`/`.patch()`/`.delete()` exist because
a future phase (the group -> MikroTik enforcement sync described in
SPEC.md) will need them, and it's easier to have the shape ready than to
retrofit it — but nothing in the foundation phase may call them. Don't wire
a write call into the discovery loop.
"""
from __future__ import annotations

import httpx


class MikroTikClient:
    def __init__(self, base_url: str, user: str, password: str, timeout: float = 8.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(user, password)
        self._timeout = timeout

    async def get(self, path: str) -> tuple[int, list | dict]:
        return await self._request("GET", path)

    async def post(self, path: str, body: dict | None = None) -> tuple[int, list | dict]:
        return await self._request("POST", path, body)

    async def patch(self, path: str, body: dict | None = None) -> tuple[int, list | dict]:
        return await self._request("PATCH", path, body)

    async def delete(self, path: str) -> tuple[int, list | dict]:
        return await self._request("DELETE", path)

    async def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> tuple[int, list | dict]:
        url = f"{self._base_url}/rest/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.request(method, url, auth=self._auth, json=body)
                content = r.json() if r.content else {}
                return r.status_code, content
        except httpx.HTTPError as exc:
            return 599, {"error": f"{exc.__class__.__name__}: {exc}"}
