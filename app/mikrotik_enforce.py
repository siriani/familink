"""Applies a device's pending_action() (see app/enforcement.py) to the real
MikroTik router. Every call here is triggered by an explicit admin click
(POST /devices/{mac}/apply-mikrotik) — nothing in this module is ever
invoked from the background discovery loop (app/sync.py stays read-only).

Every attempt, success or failure, is written to the enforcement_log table
so "what has familink actually changed on my router" stays answerable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.enforcement import PendingAction
from app.mikrotik import MikroTikClient
from app.models import Device

logger = logging.getLogger("familink.enforce")

BINDING_COMMENT = "familink"


@dataclass
class ApplyResult:
    action: PendingAction
    success: bool
    detail: str


async def _find_binding(client: MikroTikClient, mac: str) -> dict | None:
    """GET the existing ip-binding entry for this MAC, if any. Always
    checked before writing — never blind-POST a MAC that might already
    have an entry (would create a duplicate/conflicting binding).
    """
    status, body = await client.get(f"ip/hotspot/ip-binding?mac-address={mac}")
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"lookup failed (HTTP {status}): {body}")
    return body[0] if body else None


async def _create_or_fix_binding(client: MikroTikClient, device: Device) -> ApplyResult:
    existing = await _find_binding(client, device.mac)

    if existing is None:
        status, body = await client.post(
            "ip/hotspot/ip-binding",
            {"mac-address": device.mac, "server": "all", "comment": BINDING_COMMENT},
        )
        if status not in (200, 201):
            return ApplyResult("create_or_fix_binding", False, f"create failed (HTTP {status}): {body}")
        return ApplyResult("create_or_fix_binding", True, f"created binding {body.get('.id', '?')}")

    if existing.get("type") != "bypassed":
        # Already the correct shape (bound, not bypassed) — nothing to do,
        # but this shouldn't normally be reached since pending_action()
        # would have returned "none". Treat as a successful no-op.
        return ApplyResult("create_or_fix_binding", True, "binding already correct, no-op")

    binding_id = existing[".id"]
    status, body = await client.patch(f"ip/hotspot/ip-binding/{binding_id}", {"type": ""})
    if status == 200:
        return ApplyResult("create_or_fix_binding", True, f"cleared bypass on binding {binding_id}")

    # PATCH-to-unset isn't guaranteed across RouterOS versions — fall back
    # to delete + recreate, which reaches the same end state.
    logger.warning(
        "PATCH to clear type=bypassed failed (HTTP %s: %s) for %s, falling back to delete+recreate",
        status, body, device.mac,
    )
    del_status, del_body = await client.delete(f"ip/hotspot/ip-binding/{binding_id}")
    if del_status not in (200, 204):
        return ApplyResult(
            "create_or_fix_binding", False,
            f"fallback delete failed (HTTP {del_status}): {del_body} (PATCH had failed: HTTP {status}: {body})",
        )
    status2, body2 = await client.post(
        "ip/hotspot/ip-binding",
        {"mac-address": device.mac, "server": "all", "comment": BINDING_COMMENT},
    )
    if status2 not in (200, 201):
        return ApplyResult(
            "create_or_fix_binding", False,
            f"fallback recreate failed (HTTP {status2}): {body2} (old bypassed binding was already deleted!)",
        )
    return ApplyResult("create_or_fix_binding", True, f"recreated binding without bypass (fallback path)")


async def _remove_binding(client: MikroTikClient, device: Device) -> ApplyResult:
    existing = await _find_binding(client, device.mac)
    if existing is None:
        return ApplyResult("remove_binding", True, "no binding existed, no-op")

    binding_id = existing[".id"]
    status, body = await client.delete(f"ip/hotspot/ip-binding/{binding_id}")
    if status not in (200, 204):
        return ApplyResult("remove_binding", False, f"delete failed (HTTP {status}): {body}")
    return ApplyResult("remove_binding", True, f"removed binding {binding_id}")


async def apply_device(client: MikroTikClient, device: Device, action: PendingAction) -> ApplyResult:
    if action == "none":
        return ApplyResult("none", True, "nothing to do")
    if action == "create_or_fix_binding":
        return await _create_or_fix_binding(client, device)
    if action == "remove_binding":
        return await _remove_binding(client, device)
    raise ValueError(f"unknown action {action!r}")
