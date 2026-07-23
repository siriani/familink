"""MikroTik writes for the quota engine. familink does the accounting
itself (app/quota.py ticks each user's seconds_used_today every discovery
cycle) — MikroTik's job is reduced to a simple block/unblock per device,
no hotspot login or MikroTik-side counter involved at all. Verified live:
creating a `type=blocked` ip-binding does what it says (`"blocked":"true"`
in the response), same PUT-to-create/DELETE-to-remove shape already
proven for enforcement bindings.

Two operations:
- block_device / unblock_device: called once a user crosses their daily
  limit (block) or at the nightly reset (unblock) -- see
  app/quota.py:tick_and_enforce. Every write is legitimate *scheduled*
  automation (mechanical reconciliation of already-decided policy), same
  reasoning as app/mikrotik_enforce.py's night-block sync.
- sync_night_block: unchanged from the original design -- independent
  feature, reconciles an address-list's familink-owned entries to a
  group's current device IPs. Kept in this module since it's the other
  "runs every cycle, no admin click" MikroTik writer.

Tagged with a DIFFERENT comment (`familink-quota`) than
app/mikrotik_enforce.py's bindings (`familink`) -- the two systems own
different ip-binding entries and never touch each other's.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.mikrotik import MikroTikClient
from app.models import Device, Group

logger = logging.getLogger("familink.quota")

BLOCK_COMMENT = "familink-quota"
NIGHT_BLOCK_COMMENT = "familink"


@dataclass
class ApplyResult:
    success: bool
    detail: str


async def _find_binding(client: MikroTikClient, mac_upper: str) -> dict | None:
    status, body = await client.get(f"ip/hotspot/ip-binding?mac-address={mac_upper}")
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"lookup failed (HTTP {status}): {body}")
    return body[0] if body else None


async def block_device(client: MikroTikClient, device: Device) -> ApplyResult:
    """Ensure a type=blocked ip-binding exists for this device's MAC. If a
    binding already exists (e.g. from app/mikrotik_enforce.py) it's left
    alone and this reports a soft failure rather than overwriting
    someone else's binding -- mixing a quota-blocked group with a
    hotspot_required group for the same device isn't supported, see
    SPEC.md.
    """
    mac_upper = device.mac.upper()
    existing = await _find_binding(client, mac_upper)
    if existing is not None:
        if existing.get("comment") == BLOCK_COMMENT and existing.get("type") == "blocked":
            return ApplyResult(True, "already blocked")
        return ApplyResult(
            False,
            f"device already has an unrelated ip-binding (comment={existing.get('comment')!r}) "
            "-- not overwriting it. Quota-blocked groups shouldn't overlap with hotspot_required "
            "groups for the same device.",
        )
    status, body = await client.put(
        "ip/hotspot/ip-binding",
        {"mac-address": mac_upper, "server": "all", "type": "blocked", "comment": BLOCK_COMMENT},
    )
    if status not in (200, 201):
        return ApplyResult(False, f"block failed (HTTP {status}): {body}")
    # A freshly created binding lands at the end of the list -- an earlier,
    # broader rule (e.g. a subnet-wide bypass) would otherwise silently win
    # over this block. See MikroTikClient.move_to_top's docstring.
    await client.move_to_top("ip/hotspot/ip-binding", body[".id"])
    return ApplyResult(True, f"blocked (binding {body.get('.id', '?')})")


async def unblock_device(client: MikroTikClient, device: Device) -> ApplyResult:
    mac_upper = device.mac.upper()
    existing = await _find_binding(client, mac_upper)
    if existing is None or existing.get("comment") != BLOCK_COMMENT:
        return ApplyResult(True, "no familink-quota binding to remove")
    status, body = await client.delete(f"ip/hotspot/ip-binding/{existing['.id']}")
    if status not in (200, 204):
        return ApplyResult(False, f"unblock failed (HTTP {status}): {body}")
    return ApplyResult(True, "unblocked")


async def _list_familink_entries(
    client: MikroTikClient, address_list: str, comment: str
) -> dict[str, str]:
    """Returns {ip: mikrotik_id} for entries with this exact comment in
    this address-list. Never touches entries with any other comment -- a
    reused list like RESTRITO can have pre-existing manual entries that
    must be left alone.
    """
    status, body = await client.get(f"ip/firewall/address-list?list={address_list}")
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"address-list lookup failed (HTTP {status}): {body}")
    return {row["address"]: row[".id"] for row in body if row.get("comment") == comment}


async def sync_night_block(
    client: MikroTikClient, group: Group, desired_ips: set[str]
) -> ApplyResult:
    if not group.night_block_address_list:
        return ApplyResult(True, "no night_block_address_list configured, nothing to do")

    try:
        existing = await _list_familink_entries(
            client, group.night_block_address_list, NIGHT_BLOCK_COMMENT
        )
    except RuntimeError as exc:
        return ApplyResult(False, str(exc))

    to_add = desired_ips - existing.keys()
    to_remove = existing.keys() - desired_ips

    errors: list[str] = []
    for ip in to_add:
        status, body = await client.put(
            "ip/firewall/address-list",
            {
                "list": group.night_block_address_list,
                "address": ip,
                "comment": NIGHT_BLOCK_COMMENT,
            },
        )
        if status not in (200, 201):
            errors.append(f"add {ip} failed (HTTP {status}): {body}")
    for ip in to_remove:
        status, body = await client.delete(f"ip/firewall/address-list/{existing[ip]}")
        if status not in (200, 204):
            errors.append(f"remove {ip} failed (HTTP {status}): {body}")

    if errors:
        return ApplyResult(False, "; ".join(errors))
    if not to_add and not to_remove:
        return ApplyResult(True, "already in sync")
    return ApplyResult(True, f"added {len(to_add)}, removed {len(to_remove)}")
