"""familink's own captive-portal page -- replaces MikroTik's native
hotspot login entirely. MikroTik's hotspot service still does the actual
network-level interception (it's the only thing that can hold traffic
until authorized) but its login.html is replaced (one-time, out-of-band
router setup, see mikrotik-hotspot-html/README.md) with a redirect to
`/captive?mac=...&link-orig=...`.

The `mac` query param is a UX hint only -- NEVER trusted for a write.
Anyone on the LAN could otherwise craft `/captive?mac=<victim-mac>`
themselves. Instead every request re-resolves the true MAC live from
MikroTik's own hotspot host/active tables, keyed by the actual connecting
IP (`request.client.host` -- there's no reverse proxy in front of this
app, so this is the device's real LAN IP). This is the same trust
boundary the rest of the app already leans on (MikroTik's ARP/DHCP tables
are authoritative everywhere else too).
"""
from __future__ import annotations

import logging
from datetime import date
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.enforcement import desired_binding_state
from app.mikrotik import MikroTikClient
from app.mikrotik_binding import apply_binding_state
from app.models import Device, EnforcementLog, User
from app.sync import get_mikrotik_client
from app.templating import templates

logger = logging.getLogger("familink.captive")

router = APIRouter()


async def _resolve_mac_for_ip(client: MikroTikClient, ip: str) -> str | None:
    """Live lookup only -- never the app's own (up to SYNC_INTERVAL_S
    stale) devices.current_ip as the primary source, since a stale
    IP<->MAC mapping here is a real spoofing/mislinking risk, not just a
    display glitch."""
    for path in ("ip/hotspot/active", "ip/hotspot/host"):
        status, body = await client.get(path)
        if status == 200 and isinstance(body, list):
            for row in body:
                if row.get("address") == ip and row.get("mac-address"):
                    return row["mac-address"].lower()
    return None


async def _resolve_device(request: Request, db: Session) -> Device | None:
    ip = request.client.host if request.client else None
    if not ip:
        return None
    mac = None
    try:
        mac = await _resolve_mac_for_ip(get_mikrotik_client(), ip)
    except Exception:
        logger.warning("live MikroTik MAC lookup failed for ip %s", ip, exc_info=True)
    if mac is not None:
        return db.scalar(select(Device).where(Device.mac == mac))
    # MikroTik briefly unreachable -- fall back to the app's own (possibly
    # stale) mapping rather than failing the whole page outright.
    return db.scalar(select(Device).where(Device.current_ip == ip))


def _safe_continue_url(link_orig: str | None) -> str | None:
    if not link_orig:
        return None
    parsed = urlparse(link_orig)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return link_orig
    return None


@router.get("/captive", response_class=HTMLResponse)
async def page_captive(request: Request, link_orig: str = "", db: Session = Depends(get_db)):
    device = await _resolve_device(request, db)
    continue_url = _safe_continue_url(link_orig)

    if device is None:
        return templates.TemplateResponse(
            request, "captive.html", {"state": "unknown", "continue_url": continue_url}
        )

    if device.user_id is not None:
        client = get_mikrotik_client()
        await apply_binding_state(client, device, desired_binding_state(device))
        return templates.TemplateResponse(
            request,
            "captive.html",
            {"state": "connected", "user": device.user, "continue_url": continue_url},
        )

    users = list(db.scalars(select(User).order_by(User.name)))
    return templates.TemplateResponse(
        request,
        "captive.html",
        {"state": "identify", "users": users, "continue_url": continue_url},
    )


@router.post("/captive", response_class=HTMLResponse)
async def post_captive(
    request: Request,
    existing_user_id: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    birthdate: str = Form(""),
    link_orig: str = Form(""),
    db: Session = Depends(get_db),
):
    device = await _resolve_device(request, db)
    continue_url = _safe_continue_url(link_orig)

    if device is None:
        return templates.TemplateResponse(
            request, "captive.html", {"state": "unknown", "continue_url": continue_url}
        )

    if device.user_id is not None:
        # Already identified -- re-linking an already-registered device
        # (hand-me-down phone, etc.) goes through the authenticated admin
        # Owner dropdown, not this public endpoint.
        return templates.TemplateResponse(
            request,
            "captive.html",
            {"state": "connected", "user": device.user, "continue_url": continue_url},
        )

    if existing_user_id:
        user = db.get(User, int(existing_user_id))
        if user is None:
            users = list(db.scalars(select(User).order_by(User.name)))
            return templates.TemplateResponse(
                request,
                "captive.html",
                {"state": "identify", "users": users, "continue_url": continue_url, "error": "Pessoa não encontrada."},
            )
    else:
        if not name.strip():
            users = list(db.scalars(select(User).order_by(User.name)))
            return templates.TemplateResponse(
                request,
                "captive.html",
                {"state": "identify", "users": users, "continue_url": continue_url, "error": "Informe um nome."},
            )
        birthdate_val: date | None = None
        if birthdate.strip():
            birthdate_val = date.fromisoformat(birthdate.strip())
        user = User(name=name.strip(), email=(email.strip() or None), birthdate=birthdate_val)
        db.add(user)
        db.flush()

    device.user_id = user.id
    db.commit()

    client = get_mikrotik_client()
    state = desired_binding_state(device)
    result = await apply_binding_state(client, device, state)
    db.add(
        EnforcementLog(
            device_id=device.id,
            action="captive_identify",
            success=result.success,
            detail=f"identified as '{user.name}': {result.detail}",
        )
    )
    db.commit()

    return templates.TemplateResponse(
        request, "captive.html", {"state": "connected", "user": user, "continue_url": continue_url}
    )
