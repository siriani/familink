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


def pending_action(device: Device) -> PendingAction:
    desired_restricted = device.group.hotspot_required
    actually_enforced = device.mikrotik_bound and not device.mikrotik_bypassed

    if desired_restricted and not actually_enforced:
        return "create_or_fix_binding"
    if not desired_restricted and actually_enforced:
        return "remove_binding"
    return "none"


_LABELS: dict[PendingAction, str] = {
    "none": "MikroTik in sync",
    "create_or_fix_binding": "MikroTik needs: hotspot binding will be created",
    "remove_binding": "MikroTik needs: hotspot binding will be removed",
}


def pending_action_label(action: PendingAction) -> str:
    return _LABELS[action]
