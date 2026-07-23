"""MikroTik writes for the quota engine. familink does the accounting
itself (app/quota.py ticks each user's seconds_used_today every discovery
cycle) — MikroTik's job is reduced to a simple block/unblock per device,
no hotspot login or MikroTik-side counter involved at all.

- block_device / unblock_device: called once a user crosses their daily
  limit (block) or at the nightly reset (unblock) -- see
  app/quota.py:tick_and_enforce. Thin wrappers around
  app/mikrotik_binding.py:apply_binding_state, shared with
  app/mikrotik_enforce.py (admin click) and app/routers/captive.py
  (self-identification) -- a hotspot_required + quota-tracked device
  needs all three to cooperate on the SAME binding over its lifecycle
  (regular -> bypassed -> blocked -> bypassed...), not own separate ones.
- sync_night_block: unchanged from the original design -- independent
  feature, reconciles an address-list's familink-owned entries to a
  group's current device IPs. Kept in this module since it's the other
  "runs every cycle, no admin click" MikroTik writer.
"""
from __future__ import annotations

import logging

from app.enforcement import desired_binding_state
from app.mikrotik import MikroTikClient
from app.mikrotik_binding import ApplyResult, apply_binding_state
from app.models import Device, Group

logger = logging.getLogger("familink.quota")

NIGHT_BLOCK_COMMENT = "familink"


async def block_device(client: MikroTikClient, device: Device) -> ApplyResult:
    return await apply_binding_state(client, device, "blocked")


async def unblock_device(client: MikroTikClient, device: Device) -> ApplyResult:
    """Resolves back to whatever the device's binding SHOULD be once no
    longer blocked -- "bypassed" for an identified hotspot_required user,
    "none" for a quota-only/non-hotspot group -- rather than assuming one
    fixed target state (see desired_binding_state's docstring)."""
    return await apply_binding_state(client, device, desired_binding_state(device))


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
