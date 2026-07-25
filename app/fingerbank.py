"""Fingerbank.org device-identification lookups -- optional enrichment on
top of app/portscan.py's port-based guess (a different signal, shown
side by side on the device detail page, never merged into vendor_guess).

Keyed on MAC address only -- MikroTik's REST API doesn't expose a raw
DHCP fingerprint (option 55 parameter-request list) today, so the richer
`dhcp_fingerprint`/`dhcp_vendor` parameters Fingerbank's API also accepts
aren't available here. A MAC-only lookup is still meaningfully more
accurate than the port-based guess for manufacturer identification,
since it's backed by Fingerbank's full OUI + device-combinations
database rather than a dozen hand-picked port hints -- but it can't tell
two devices from the same manufacturer apart the way a full fingerprint
could. Worth revisiting if MikroTik ever exposes that data via REST.

Opt-in: the API key is set by the admin at /settings
(app/routers/settings.py), not an env var -- this is user-facing
configuration a non-technical familink admin should be able to change
without touching .env or redeploying. No key configured = every lookup
is a fast no-op, never a failed network call.

Every entry point here is self-contained (opens its own DB session via
app.db.session_scope, same as app/portscan.py) so it can be called
equally from a request handler or the async discovery loop without
threading a session through either way.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app.settings import get_setting

logger = logging.getLogger("familink.fingerbank")

_API_URL = "https://api.fingerbank.org/api/v2/combinations/interrogate"
_TIMEOUT_S = 10.0


def _read_api_key() -> str | None:
    from app.db import session_scope

    with session_scope() as session:
        return get_setting(session, "fingerbank_api_key")


async def lookup_mac(mac: str) -> dict | None:
    """Returns {"device_name", "manufacturer", "score"} on a match, or
    None if no key is configured, the request fails, or Fingerbank has
    nothing for this MAC. Never raises -- a Fingerbank hiccup is a
    missed enrichment, not a reason to break device discovery/display.
    """
    api_key = await asyncio.to_thread(_read_api_key)
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(_API_URL, params={"key": api_key, "mac": mac})
    except httpx.HTTPError:
        logger.warning("fingerbank lookup failed for %s", mac, exc_info=True)
        return None

    if resp.status_code != 200:
        logger.warning(
            "fingerbank lookup for %s: HTTP %s: %s", mac, resp.status_code, resp.text[:200]
        )
        return None

    body = resp.json()
    device = body.get("device") or {}
    manufacturer = body.get("manufacturer") or {}
    return {
        "device_name": device.get("name") or None,
        "manufacturer": manufacturer.get("name") or None,
        "score": body.get("score"),
    }


def _store_result(device_id: int, result: dict | None) -> None:
    from app.db import session_scope
    from app.models import Device

    with session_scope() as session:
        device = session.get(Device, device_id)
        if device is None:
            return  # device was deleted between lookup trigger and completion
        device.fingerbank_checked_at = datetime.now(timezone.utc)
        if result is not None:
            device.fingerbank_device_name = result["device_name"]
            device.fingerbank_manufacturer = result["manufacturer"]
            device.fingerbank_score = result["score"]
        session.commit()


async def enrich_and_store(device_id: int, mac: str) -> None:
    result = await lookup_mac(mac)
    await asyncio.to_thread(_store_result, device_id, result)
