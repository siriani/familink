"""Best-effort push of an admin-set hostname override
(app/models.py:Device.hostname_override) to the matching MikroTik DHCP
lease's `comment` field -- so the label is visible from Winbox/MikroTik
directly too, not just familink's own UI. A different MikroTik resource
than app/mikrotik_binding.py (`ip/dhcp-server/lease`, not
`ip/hotspot/ip-binding`) and a different kind of write (a label, not
enforcement), so it gets its own small module rather than being folded
into that one.

No lease for this MAC (a static-IP device with no DHCP lease at all) is
NOT a failure -- there's nothing on MikroTik to write the label to, and
that's an expected, ordinary case, not an error to surface loudly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.mikrotik import MikroTikClient


@dataclass
class LeaseUpdateResult:
    success: bool
    detail: str


async def push_hostname_to_lease(
    client: MikroTikClient, mac: str, hostname: str
) -> LeaseUpdateResult:
    # ARMADILHA (same one documented in app/mikrotik_binding.py): the
    # mac-address REST filter is case-sensitive and MikroTik stores/
    # returns MACs uppercase.
    mac_upper = mac.upper()
    status, body = await client.get(f"ip/dhcp-server/lease?mac-address={mac_upper}")
    if status != 200 or not isinstance(body, list):
        return LeaseUpdateResult(False, f"lease lookup failed (HTTP {status}): {body}")
    if not body:
        return LeaseUpdateResult(
            True, "no DHCP lease for this MAC -- nothing to update on MikroTik"
        )
    lease_id = body[0][".id"]
    patch_status, patch_body = await client.patch(
        f"ip/dhcp-server/lease/{lease_id}", {"comment": hostname}
    )
    if patch_status != 200:
        return LeaseUpdateResult(
            False, f"lease comment update failed (HTTP {patch_status}): {patch_body}"
        )
    return LeaseUpdateResult(True, f"updated lease {lease_id} comment to {hostname!r}")
