"""Single owner of every MikroTik `ip-binding` entry familink creates --
the one place that reads a device's `desired_binding_state()`
(app/enforcement.py) and makes MikroTik's actual state match it. Used by
app/mikrotik_enforce.py (admin-click), app/mikrotik_quota.py (quota
block/unblock), and app/routers/captive.py (self-identification) -- these
three used to be independent, mutually-suspicious writers (enforcement
tagged its bindings "familink", quota used a different "familink-quota"
tag and refused to touch anything not carrying its own tag) even though a
single `hotspot_required` + quota-tracked device genuinely needs all three
to cooperate on the SAME binding over its lifecycle. One tag, one writer.

Verified live (23/jul/2026): a freshly created binding lands at the END of
RouterOS's ip-binding list, which is evaluated top-to-bottom, first match
wins -- a pre-existing broad rule earlier in the list (e.g. a subnet-wide
bypass) can silently shadow it. Every create/fix here calls
MikroTikClient.move_to_top() so that never happens again.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.enforcement import BindingState
from app.mikrotik import MikroTikClient
from app.models import Device

logger = logging.getLogger("familink.binding")

BINDING_COMMENT = "familink"

# MikroTik's own `type` field for a binding in each of our states. "regular"
# is represented by omitting `type` entirely on create (verified live) but
# by the explicit string "regular" on PATCH (verified live: PATCHing
# type="" fails with "ambiguous value of type").
_TYPE_FOR_STATE = {"regular": "regular", "bypassed": "bypassed", "blocked": "blocked"}


@dataclass
class ApplyResult:
    success: bool
    detail: str


async def _find_binding(client: MikroTikClient, mac_upper: str) -> dict | None:
    """ARMADILHA verified live: the `mac-address` REST filter is
    case-sensitive and MikroTik stores/returns MACs uppercase -- callers
    must pass an already-uppercased MAC (see apply_binding_state below,
    the only entry point)."""
    status, body = await client.get(f"ip/hotspot/ip-binding?mac-address={mac_upper}")
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"lookup failed (HTTP {status}): {body}")
    return body[0] if body else None


def _current_state(binding: dict) -> str:
    t = binding.get("type")
    if t == "bypassed" or t == "blocked":
        return t
    return "regular"


async def apply_binding_state(
    client: MikroTikClient, device: Device, state: BindingState
) -> ApplyResult:
    mac_upper = device.mac.upper()
    existing = await _find_binding(client, mac_upper)

    if existing is not None and existing.get("comment") != BINDING_COMMENT:
        # A binding we didn't create (hand-managed, or pre-dates familink).
        # Never overwrite someone else's config -- same caution as before.
        if state == "none":
            return ApplyResult(True, "unrelated binding exists, leaving it alone, no-op")
        return ApplyResult(
            False,
            f"device already has an unrelated ip-binding (comment={existing.get('comment')!r}) "
            "-- not touching it. Remove or retag it manually if familink should manage this device.",
        )

    if state == "none":
        if existing is None:
            return ApplyResult(True, "no binding needed, none exists, no-op")
        status, body = await client.delete(f"ip/hotspot/ip-binding/{existing['.id']}")
        if status not in (200, 204):
            return ApplyResult(False, f"remove failed (HTTP {status}): {body}")
        return ApplyResult(True, f"removed binding {existing['.id']}")

    if existing is None:
        body_out: dict = {"mac-address": mac_upper, "server": "all", "comment": BINDING_COMMENT}
        if state != "regular":
            body_out["type"] = _TYPE_FOR_STATE[state]
        status, body = await client.put("ip/hotspot/ip-binding", body_out)
        if status not in (200, 201):
            return ApplyResult(False, f"create failed (HTTP {status}): {body}")
        await client.move_to_top("ip/hotspot/ip-binding", body[".id"])
        return ApplyResult(True, f"created binding {body.get('.id', '?')} as {state}")

    if _current_state(existing) == state:
        await client.move_to_top("ip/hotspot/ip-binding", existing[".id"])
        return ApplyResult(True, f"already {state}, no-op")

    binding_id = existing[".id"]
    status, body = await client.patch(
        f"ip/hotspot/ip-binding/{binding_id}", {"type": _TYPE_FOR_STATE[state]}
    )
    if status == 200:
        await client.move_to_top("ip/hotspot/ip-binding", binding_id)
        return ApplyResult(True, f"changed binding {binding_id} to {state}")

    # Belt-and-suspenders in case a future RouterOS version changes this
    # again: fall back to delete + recreate, which reaches the same end
    # state via calls already proven to work above.
    logger.warning(
        "PATCH type=%s failed (HTTP %s: %s) for %s, falling back to delete+recreate",
        state, status, body, mac_upper,
    )
    del_status, del_body = await client.delete(f"ip/hotspot/ip-binding/{binding_id}")
    if del_status not in (200, 204):
        return ApplyResult(
            False,
            f"fallback delete failed (HTTP {del_status}): {del_body} (PATCH had failed: HTTP {status}: {body})",
        )
    body_out = {"mac-address": mac_upper, "server": "all", "comment": BINDING_COMMENT}
    if state != "regular":
        body_out["type"] = _TYPE_FOR_STATE[state]
    status2, body2 = await client.put("ip/hotspot/ip-binding", body_out)
    if status2 not in (200, 201):
        return ApplyResult(
            False,
            f"fallback recreate failed (HTTP {status2}): {body2} (old binding was already deleted!)",
        )
    await client.move_to_top("ip/hotspot/ip-binding", body2[".id"])
    return ApplyResult(True, f"recreated binding as {state} (fallback path)")
