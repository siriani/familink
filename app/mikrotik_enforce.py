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


async def _find_binding(client: MikroTikClient, mac_upper: str) -> dict | None:
    """GET the existing ip-binding entry for this MAC, if any. Always
    checked before writing — never blind-create a MAC that might already
    have an entry (would create a duplicate/conflicting binding).

    ARMADILHA verified live: the `mac-address` REST filter is case-
    sensitive and MikroTik stores/returns MACs uppercase — a lowercase
    query silently matches nothing. Callers must pass an already-uppercased
    MAC (see apply_device below, which is the only entry point).
    """
    status, body = await client.get(f"ip/hotspot/ip-binding?mac-address={mac_upper}")
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"lookup failed (HTTP {status}): {body}")
    return body[0] if body else None


async def _create_or_fix_binding(client: MikroTikClient, mac_upper: str) -> ApplyResult:
    existing = await _find_binding(client, mac_upper)

    if existing is None:
        status, body = await client.put(
            "ip/hotspot/ip-binding",
            {"mac-address": mac_upper, "server": "all", "comment": BINDING_COMMENT},
        )
        if status not in (200, 201):
            return ApplyResult("create_or_fix_binding", False, f"create failed (HTTP {status}): {body}")
        return ApplyResult("create_or_fix_binding", True, f"created binding {body.get('.id', '?')}")

    if existing.get("type") != "bypassed":
        # Already the correct shape (bound, not bypassed) — nothing to do,
        # but this shouldn't normally be reached since pending_action()
        # would have returned "none". Treat as a successful no-op.
        return ApplyResult("create_or_fix_binding", True, "binding already correct, no-op")

    # ARMADILHA verified live: PATCHing type="" to unset it fails with
    # "ambiguous value of type" — the working value is the explicit
    # "regular" enum member, not an empty string.
    binding_id = existing[".id"]
    status, body = await client.patch(f"ip/hotspot/ip-binding/{binding_id}", {"type": "regular"})
    if status == 200:
        return ApplyResult("create_or_fix_binding", True, f"cleared bypass on binding {binding_id}")

    # Belt-and-suspenders in case a future RouterOS version changes this
    # again: fall back to delete + recreate, which reaches the same end
    # state via calls already proven to work above.
    logger.warning(
        "PATCH type=regular failed (HTTP %s: %s) for %s, falling back to delete+recreate",
        status, body, mac_upper,
    )
    del_status, del_body = await client.delete(f"ip/hotspot/ip-binding/{binding_id}")
    if del_status not in (200, 204):
        return ApplyResult(
            "create_or_fix_binding", False,
            f"fallback delete failed (HTTP {del_status}): {del_body} (PATCH had failed: HTTP {status}: {body})",
        )
    status2, body2 = await client.put(
        "ip/hotspot/ip-binding",
        {"mac-address": mac_upper, "server": "all", "comment": BINDING_COMMENT},
    )
    if status2 not in (200, 201):
        return ApplyResult(
            "create_or_fix_binding", False,
            f"fallback recreate failed (HTTP {status2}): {body2} (old bypassed binding was already deleted!)",
        )
    return ApplyResult("create_or_fix_binding", True, "recreated binding without bypass (fallback path)")


async def _remove_binding(client: MikroTikClient, mac_upper: str) -> ApplyResult:
    existing = await _find_binding(client, mac_upper)
    if existing is None:
        return ApplyResult("remove_binding", True, "no binding existed, no-op")

    binding_id = existing[".id"]
    status, body = await client.delete(f"ip/hotspot/ip-binding/{binding_id}")
    if status not in (200, 204):
        return ApplyResult("remove_binding", False, f"delete failed (HTTP {status}): {body}")
    return ApplyResult("remove_binding", True, f"removed binding {binding_id}")


async def apply_device(client: MikroTikClient, device: Device, action: PendingAction) -> ApplyResult:
    mac_upper = device.mac.upper()
    if action == "none":
        return ApplyResult("none", True, "nothing to do")
    if action == "create_or_fix_binding":
        return await _create_or_fix_binding(client, mac_upper)
    if action == "remove_binding":
        return await _remove_binding(client, mac_upper)
    raise ValueError(f"unknown action {action!r}")
