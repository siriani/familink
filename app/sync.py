"""Read-only MikroTik discovery loop.

Polls four MikroTik REST endpoints every SYNC_INTERVAL_S, merges them by
normalized MAC address, and upserts the `devices` table. Never calls a
write verb on MikroTik (see the note in app/mikrotik.py) and never touches
the admin-owned fields on an existing device (group_id, user_id, notes) —
only `current_ip`, `hostname`, `is_online`, `mikrotik_bound`,
`mikrotik_bypassed`, and `last_seen` are refreshed on every cycle.

A MikroTik hiccup (timeout, malformed response, router briefly
unreachable) must never crash this loop — log and retry next cycle.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import MIKROTIK_PASSWORD, MIKROTIK_URL, MIKROTIK_USER, SYNC_INTERVAL_S
from app.db import session_scope
from app.mikrotik import MikroTikClient
from app.models import Device, Group

logger = logging.getLogger("familink.sync")

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def normalize_mac(raw: str) -> str | None:
    """Lowercase, colon-separated. Returns None if it doesn't look like a MAC
    (MikroTik responses can carry empty mac-address fields for some entries).
    """
    if not raw:
        return None
    mac = raw.strip().lower().replace("-", ":")
    return mac if _MAC_RE.match(mac) else None


def get_mikrotik_client() -> MikroTikClient:
    return MikroTikClient(MIKROTIK_URL, MIKROTIK_USER, MIKROTIK_PASSWORD)


def merge_mikrotik_views(
    leases: list[dict], active: list[dict], hosts: list[dict], bindings: list[dict]
) -> dict[str, dict]:
    """One row per normalized MAC, merged from all four MikroTik views.

    - hostname/ip: from DHCP lease (fallback: ip-binding comment)
    - is_online: MAC present in `active` (authenticated hotspot session) OR
      `hosts` (broader "seen on network", covers bypassed devices too,
      since those never show up in `active`)
    - mikrotik_bound / mikrotik_bypassed: presence + type=bypassed in
      ip-binding — informational only in this phase.
    """
    merged: dict[str, dict] = {}

    def entry(mac: str) -> dict:
        return merged.setdefault(
            mac,
            {
                "ip": None,
                "hostname": None,
                "is_online": False,
                "mikrotik_bound": False,
                "mikrotik_bypassed": False,
            },
        )

    for lease in leases or []:
        mac = normalize_mac(lease.get("mac-address", ""))
        if not mac:
            continue
        e = entry(mac)
        e["ip"] = lease.get("address") or e["ip"]
        e["hostname"] = lease.get("host-name") or lease.get("comment") or e["hostname"]

    online_macs: set[str] = set()
    for row in (active or []) + (hosts or []):
        mac = normalize_mac(row.get("mac-address", ""))
        if mac:
            online_macs.add(mac)
    for mac in online_macs:
        entry(mac)["is_online"] = True

    for binding in bindings or []:
        mac = normalize_mac(binding.get("mac-address", ""))
        if not mac:
            continue
        e = entry(mac)
        e["mikrotik_bound"] = True
        e["mikrotik_bypassed"] = binding.get("type") == "bypassed"
        if not e["hostname"]:
            e["hostname"] = binding.get("comment") or None

    return merged


def _default_group_id(session) -> int:
    group_id = session.scalar(select(Group.id).where(Group.is_default.is_(True)))
    if group_id is None:
        raise RuntimeError(
            "no group has is_default=true — check the seed data in "
            "migrations/versions/0001_initial_schema.py"
        )
    return group_id


def upsert_devices(merged: dict[str, dict]) -> None:
    """Sync DB write, run off the event loop via asyncio.to_thread by the
    caller. Opens its own session so it's safe to call from a worker thread.
    """
    with session_scope() as session:
        default_group_id = _default_group_id(session)
        now = datetime.now(timezone.utc)
        for mac, info in merged.items():
            device = session.scalar(select(Device).where(Device.mac == mac))
            if device is None:
                device = Device(mac=mac, group_id=default_group_id)
                session.add(device)
            device.current_ip = info["ip"] or device.current_ip
            device.hostname = info["hostname"] or device.hostname
            device.is_online = info["is_online"]
            device.mikrotik_bound = info["mikrotik_bound"]
            device.mikrotik_bypassed = info["mikrotik_bypassed"]
            device.last_seen = now
        session.commit()


async def run_discovery_cycle(client: MikroTikClient) -> None:
    _, leases = await client.get("ip/dhcp-server/lease")
    _, active = await client.get("ip/hotspot/active")
    _, hosts = await client.get("ip/hotspot/host")
    _, bindings = await client.get("ip/hotspot/ip-binding")

    for name, val in (("lease", leases), ("active", active), ("hosts", hosts), ("bindings", bindings)):
        if not isinstance(val, list):
            logger.warning("mikrotik %s returned non-list (router unreachable or auth failed?): %r", name, val)

    merged = merge_mikrotik_views(
        leases if isinstance(leases, list) else [],
        active if isinstance(active, list) else [],
        hosts if isinstance(hosts, list) else [],
        bindings if isinstance(bindings, list) else [],
    )
    await asyncio.to_thread(upsert_devices, merged)
    logger.info("discovery cycle: %d devices merged", len(merged))


_last_cycle_at: datetime | None = None


def last_cycle_age_s() -> float | None:
    if _last_cycle_at is None:
        return None
    return (datetime.now(timezone.utc) - _last_cycle_at).total_seconds()


async def discovery_loop(interval_s: float = SYNC_INTERVAL_S) -> None:
    global _last_cycle_at
    client = get_mikrotik_client()
    while True:
        try:
            await run_discovery_cycle(client)
            _last_cycle_at = datetime.now(timezone.utc)
        except Exception:
            logger.exception("discovery cycle failed")
        await asyncio.sleep(interval_s)
