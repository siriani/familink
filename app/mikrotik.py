"""Async MikroTik RouterOS REST API client.

Ported from the existing hotspot-admin/server.py's `mk()` helper (Basic
Auth, `{base}/rest/{path}`, JSON body, short timeout, normalize errors into
a (status, body) tuple), just made httpx-async to match this project's
FastAPI style.

`.get()` is used by the read-only discovery loop (app/sync.py) — that loop
must never call a write verb. `.put()`/`.patch()`/`.delete()`/`.post()`
are used by app/mikrotik_enforce.py (admin-click-triggered) and
app/mikrotik_quota.py (legitimately *scheduled* automation — see that
module's docstring for why that's a different class of write than
enforcement's). Keep writes deliberate either way: an explicit click or a
documented schedule, never an incidental side effect of a read loop.

ARMADILHA verified live against RouterOS 7.23: the REST API is NOT plain
REST-conventional.
- Creating a new *resource* (an ip-binding, an address-list entry, ...) is
  **PUT**, not POST (POST on a collection path returns
  `{"detail": "no such command", "error": 400}`).
- Triggering an *action* that isn't resource creation (e.g.
  `ip/hotspot/user/reset-counters`) genuinely IS **POST** — verified live,
  returns 200. Don't conflate the two just because both write.
- GET/PATCH/DELETE behave as expected.
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

    async def put(self, path: str, body: dict | None = None) -> tuple[int, list | dict]:
        """Create a new entry. MikroTik's REST API uses PUT for this, not
        POST — see the module docstring."""
        return await self._request("PUT", path, body)

    async def post(self, path: str, body: dict | None = None) -> tuple[int, list | dict]:
        """Trigger an action endpoint (e.g. reset-counters) -- NOT for
        creating a resource, that's .put(). See the module docstring."""
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
