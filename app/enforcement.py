"""Pure diff computation between a device's desired access class (its
group's `hotspot_required`) and what's actually enforced on MikroTik right
now (`device.mikrotik_bound` / `device.mikrotik_bypassed`, kept fresh every
cycle by app/sync.py). No MikroTik calls here — this only reads fields
already in the `devices` table, so it's cheap enough to compute on every
page render.

See app/mikrotik_enforce.py for the (explicit, per-device, admin-triggered)
code that actually writes to MikroTik based on this diff.
"""
from __future__ import annotations

from typing import Literal

from app.models import Device

PendingAction = Literal["none", "create_or_fix_binding", "remove_binding"]

BindingState = Literal["none", "regular", "bypassed", "blocked"]


def desired_binding_state(device: Device) -> BindingState:
    """The one place that decides what a device's MikroTik ip-binding
    SHOULD look like right now -- shared by app/mikrotik_enforce.py (admin
    click), app/mikrotik_quota.py (block on quota exhaustion / unblock on
    reset), and app/routers/captive.py (self-identification). A
    `hotspot_required` device goes through one lifecycle on a single
    binding: "regular" (not yet identified, forced through the captive
    page) -> "bypassed" (identified, quota OK or no quota set) ->
    "blocked" (quota exhausted) -> "bypassed" again next day, without ever
    re-identifying, since the MAC->user link persists in the DB.
    """
    if not device.group.hotspot_required:
        return "none"
    if device.user_id is not None and device.user.blocked:
        return "blocked"
    if device.user_id is not None:
        return "bypassed"
    return "regular"


def pending_action(device: Device) -> PendingAction:
    desired = desired_binding_state(device)

    if desired == "none":
        bound = device.mikrotik_bound and not device.mikrotik_bypassed
        return "remove_binding" if bound else "none"
    if desired == "regular":
        correct = device.mikrotik_bound and not device.mikrotik_bypassed
        return "none" if correct else "create_or_fix_binding"
    if desired == "bypassed":
        correct = device.mikrotik_bound and device.mikrotik_bypassed
        return "none" if correct else "create_or_fix_binding"
    # desired == "blocked": device.mikrotik_bound/mikrotik_bypassed can't
    # currently distinguish "blocked" from "regular" (both are
    # bound=True/bypassed=False) -- quota's own tick_and_enforce already
    # keeps this in sync every cycle regardless, so it's out of scope for
    # this admin-click button (would need a tracked mikrotik_blocked
    # column to show accurately here -- deferred, see SPEC.md).
    return "none"


_LABELS: dict[PendingAction, str] = {
    "none": "MikroTik in sync",
    "create_or_fix_binding": "MikroTik needs: hotspot binding will be created",
    "remove_binding": "MikroTik needs: hotspot binding will be removed",
}


def pending_action_label(action: PendingAction) -> str:
    return _LABELS[action]
