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
from app.mikrotik_enforce import BINDING_COMMENT
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

    - hostname: from DHCP lease (fallback: ip-binding comment)
    - ip: from DHCP lease if there is one, but ALSO from `active`/`hosts` —
      those two reflect whatever MikroTik currently sees on the wire (ARP-
      level, via the hotspot host table) regardless of whether the device
      ever took a DHCP lease at all. This matters for devices with a
      manually-configured static IP outside the DHCP pool (seen live: a
      camera with `NetWork.NetDHCP=false` and a hand-set address) — those
      never appear in `lease` but do show up in `hosts` with a real
      `address` field, so relying on `lease` alone silently drops their IP.
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

    for row in (active or []) + (hosts or []):
        mac = normalize_mac(row.get("mac-address", ""))
        if not mac:
            continue
        e = entry(mac)
        e["is_online"] = True
        e["ip"] = row.get("address") or e["ip"]

    for binding in bindings or []:
        mac = normalize_mac(binding.get("mac-address", ""))
        if not mac:
            continue
        e = entry(mac)
        e["mikrotik_bound"] = True
        e["mikrotik_bypassed"] = binding.get("type") == "bypassed"
        comment = binding.get("comment") or None
        # BUG found live (22/jul/2026): app/mikrotik_enforce.py tags every
        # binding IT creates with comment=BINDING_COMMENT ("familink") --
        # that's ownership metadata, not a device name. Treating it as a
        # hostname fallback overwrote a real device's displayed name with
        # the literal string "familink" the moment enforcement was applied
        # to it (caught via a bogus "familink" entity in Home Assistant).
        if not e["hostname"] and comment != BINDING_COMMENT:
            e["hostname"] = comment

    return merged


def _default_group_id(session) -> int:
    group_id = session.scalar(select(Group.id).where(Group.is_default.is_(True)))
    if group_id is None:
        raise RuntimeError(
            "no group has is_default=true — check the seed data in "
            "migrations/versions/0001_initial_schema.py"
        )
    return group_id


def upsert_devices(merged: dict[str, dict]) -> list[tuple[int, str]]:
    """Sync DB write, run off the event loop via asyncio.to_thread by the
    caller. Opens its own session so it's safe to call from a worker thread.

    Returns (device_id, ip) for every device INSERTED this cycle (not
    updated) that has a known IP — the caller uses this to kick off an
    automatic port scan for newly-discovered devices only, see
    app/portscan.py.
    """
    newly_created: list[tuple[int, str]] = []
    with session_scope() as session:
        default_group_id = _default_group_id(session)
        now = datetime.now(timezone.utc)
        for mac, info in merged.items():
            device = session.scalar(select(Device).where(Device.mac == mac))
            is_new = device is None
            if device is None:
                device = Device(mac=mac, group_id=default_group_id)
                session.add(device)
            device.current_ip = info["ip"] or device.current_ip
            device.hostname = info["hostname"] or device.hostname
            device.is_online = info["is_online"]
            device.mikrotik_bound = info["mikrotik_bound"]
            device.mikrotik_bypassed = info["mikrotik_bypassed"]
            device.last_seen = now
            if is_new and device.current_ip:
                session.flush()  # need device.id before commit
                newly_created.append((device.id, device.current_ip))
        session.commit()
    return newly_created


def _load_night_block_groups() -> list[tuple[Group, set[str]]]:
    """Every group with a night_block_address_list configured, paired
    with the current_ip of every device in it (regardless of user
    linkage or hotspot_required -- see app/mikrotik_quota.py module
    docstring for why night-block is independent of quota).
    """
    with session_scope() as session:
        groups = list(
            session.scalars(select(Group).where(Group.night_block_address_list.is_not(None)))
        )
        result = []
        for group in groups:
            ips = set(
                session.scalars(
                    select(Device.current_ip).where(
                        Device.group_id == group.id, Device.current_ip.is_not(None)
                    )
                )
            )
            result.append((group, ips))
        return result


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
    newly_created = await asyncio.to_thread(upsert_devices, merged)
    logger.info("discovery cycle: %d devices merged, %d new", len(merged), len(newly_created))

    if newly_created:
        from app.portscan import scan_and_store  # local import: avoids a
        # hard dependency from the read-only discovery loop on the scanner
        # module unless a new device actually triggers it.

        for device_id, ip in newly_created:
            asyncio.create_task(scan_and_store(device_id, ip))

    from app.mqtt_publish import publish_all  # local import, same reason as above

    await publish_all()  # no-op if MQTT_HOST isn't configured

    from app.mikrotik_quota import sync_night_block  # local import, same reason as above

    night_block_groups = await asyncio.to_thread(_load_night_block_groups)
    for group, ips in night_block_groups:
        result = await sync_night_block(client, group, ips)
        if not result.success:
            logger.warning("night-block sync failed for group '%s': %s", group.name, result.detail)

    from app.quota import tick_and_enforce  # local import, same reason as above

    await tick_and_enforce()


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
