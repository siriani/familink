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
import time
from datetime import date, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.enforcement import desired_binding_state
from app.i18n import get_translations
from app.mikrotik import MikroTikClient
from app.mikrotik_binding import apply_binding_state
from app.models import Device, EnforcementLog, User
from app.pin import hash_pin, is_valid_pin_format, verify_pin
from app.sync import get_mikrotik_client
from app.templating import templates

logger = logging.getLogger("familink.captive")

router = APIRouter()

# Keyed by target user_id, not by device/IP -- a determined attacker can
# spoof a MAC trivially, so per-device keying would be no real defense;
# per-user keying protects the person regardless of which "device" is
# doing the guessing, at the minor cost of a family member's own typos
# counting toward the same lockout. In-memory and process-local (not
# DB-backed) is a deliberate trade-off, same class of decision as the
# rest of familink's read-mostly runtime state -- a restart resetting
# everyone's attempt counters is harmless, unlike losing quota data.
_PIN_MAX_ATTEMPTS = 5
_PIN_LOCKOUT_S = 15 * 60
_pin_attempts: dict[int, tuple[int, float]] = {}


def _pin_locked_out(user_id: int) -> bool:
    count, first_attempt = _pin_attempts.get(user_id, (0, 0.0))
    if count < _PIN_MAX_ATTEMPTS:
        return False
    if time.monotonic() - first_attempt > _PIN_LOCKOUT_S:
        del _pin_attempts[user_id]
        return False
    return True


def _record_pin_failure(user_id: int) -> None:
    count, first_attempt = _pin_attempts.get(user_id, (0, time.monotonic()))
    _pin_attempts[user_id] = (count + 1, first_attempt)


def _clear_pin_attempts(user_id: int) -> None:
    _pin_attempts.pop(user_id, None)


_TOP_N = 5


def _pin_selectable_users(db: Session) -> list[User]:
    """Only people with a PIN set are offered on the /captive picker --
    see app/models.py:User's docstring for why a PIN is required here at
    all. A person with no PIN yet simply can't be self-selected; an admin
    sets one from that person's edit page in the (authenticated) panel.

    Sorted by most-recently-active first (max `last_seen` across the
    person's own devices, stashed on `.last_seen_at` -- not a mapped
    column, just a plain attribute for the template) so whoever's most
    likely trying to connect right now shows up first, ahead of anyone
    who hasn't been online in a while. Nobody with a PIN starts with zero
    devices in practice (self-registration links a device in the same
    step that sets the PIN), but the None-safe sort key handles it anyway.
    """
    users = list(
        db.scalars(
            select(User)
            .where(User.pin_hash.is_not(None))
            .options(selectinload(User.devices))
            .order_by(User.name)
        )
    )
    for u in users:
        seen = [d.last_seen for d in u.devices if d.last_seen is not None]
        u.last_seen_at = max(seen) if seen else None
    users.sort(key=lambda u: u.last_seen_at or datetime.min, reverse=True)
    return users


def _render_identify(
    request: Request,
    db: Session,
    continue_url: str | None,
    view: str,
    error: str | None = None,
):
    if view == "new":
        return templates.TemplateResponse(
            request,
            "captive.html",
            {"state": "identify", "view": "new", "continue_url": continue_url, "error": error},
        )
    all_users = _pin_selectable_users(db)
    show_all = view == "all"
    return templates.TemplateResponse(
        request,
        "captive.html",
        {
            "state": "identify",
            "view": "all" if show_all else "",
            "users": all_users if show_all else all_users[:_TOP_N],
            "has_more": not show_all and len(all_users) > _TOP_N,
            "continue_url": continue_url,
            "error": error,
        },
    )


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
async def page_captive(
    request: Request, link_orig: str = "", view: str = "", db: Session = Depends(get_db)
):
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

    return _render_identify(request, db, continue_url, view)


@router.post("/captive", response_class=HTMLResponse)
async def post_captive(
    request: Request,
    existing_user_id: str = Form(""),
    pin: str = Form(""),
    view: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    birthdate: str = Form(""),
    new_pin: str = Form(""),
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

    t = get_translations(request.state.locale)

    if existing_user_id:
        user = db.get(User, int(existing_user_id))
        if user is None or user.pin_hash is None:
            return _render_identify(request, db, continue_url, view, t.gettext("Person not found."))
        if _pin_locked_out(user.id):
            return _render_identify(
                request, db, continue_url, view,
                t.gettext("Too many attempts. Try again in a few minutes."),
            )
        if not verify_pin(pin, user.pin_hash):
            _record_pin_failure(user.id)
            return _render_identify(request, db, continue_url, view, t.gettext("Incorrect PIN."))
        _clear_pin_attempts(user.id)
    else:
        if not name.strip():
            return _render_identify(request, db, continue_url, "new", t.gettext("Please enter a name."))
        if not is_valid_pin_format(new_pin):
            return _render_identify(
                request, db, continue_url, "new", t.gettext("PIN must be 4 digits.")
            )
        birthdate_val: date | None = None
        if birthdate.strip():
            birthdate_val = date.fromisoformat(birthdate.strip())
        user = User(
            name=name.strip(),
            email=(email.strip() or None),
            birthdate=birthdate_val,
            pin_hash=hash_pin(new_pin),
        )
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
