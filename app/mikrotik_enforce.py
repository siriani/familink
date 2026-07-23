"""Applies a device's desired_binding_state() (see app/enforcement.py) to
the real MikroTik router. Every call here is triggered by an explicit
admin click (POST /devices/{mac}/apply-mikrotik) — nothing in this module
is ever invoked from the background discovery loop (app/sync.py stays
read-only).

Every attempt, success or failure, is written to the enforcement_log table
so "what has familink actually changed on my router" stays answerable.

The actual binding read/write logic lives in app/mikrotik_binding.py,
shared with app/mikrotik_quota.py (block/unblock) and app/routers/captive.py
(self-identification) — those three all need to cooperate on the SAME
ip-binding over a device's lifecycle, not each own a separate one.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.enforcement import PendingAction, desired_binding_state
from app.mikrotik import MikroTikClient
from app.mikrotik_binding import apply_binding_state
from app.models import Device


@dataclass
class ApplyResult:
    action: str
    success: bool
    detail: str


async def apply_device(client: MikroTikClient, device: Device, action: PendingAction) -> ApplyResult:
    """`action` (from pending_action()) is accepted for backwards
    compatibility with the existing call site but not otherwise used --
    desired_binding_state(device) is recomputed here so this always
    reflects the device's current group/user/blocked state, not a
    possibly-stale snapshot taken earlier in the request."""
    state = desired_binding_state(device)
    result = await apply_binding_state(client, device, state)
    return ApplyResult(state, result.success, result.detail)
