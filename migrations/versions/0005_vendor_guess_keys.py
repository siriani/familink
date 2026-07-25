"""vendor_guess: literal English labels -> stable i18n keys

device.vendor_guess used to store the literal display label directly (e.g.
"Camera (ONVIF)") -- see app/portscan.py's _TYPE_HINTS. That makes it
impossible to translate: the value IS the English text. This migration
backfills the 13 known existing label values to the new stable keys
(app/portscan.py's TYPE_LABEL_MSGIDS is the source of truth going forward;
display translation now happens at render time, see
app/routers/devices.py). Deterministic 1:1 mapping over a closed set --
vendor_guess is only ever set by app/portscan.py's scan, never
user-editable via any form, so there's no "unexpected hand-typed value"
case to worry about; anything not in the CASE (a legacy/unknown value)
passes through unchanged.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (old literal label, new stable key) -- must match app/portscan.py's
# TYPE_LABEL_MSGIDS exactly.
_LABEL_TO_KEY = [
    ("DVR/NVR (XM/Xiongmai-style)", "dvr_nvr_xm"),
    ("DVR/NVR (Dahua-compatible)", "dvr_nvr_dahua"),
    ("Camera (ONVIF)", "camera_onvif"),
    ("Camera/streaming (RTSP)", "camera_rtsp"),
    ("Printer (JetDirect)", "printer_jetdirect"),
    ("Printer (IPP)", "printer_ipp"),
    ("Home Assistant", "home_assistant"),
    ("MQTT broker", "mqtt_broker"),
    ("MySQL/MariaDB", "mysql_mariadb"),
    ("PostgreSQL", "postgresql"),
    ("VNC", "vnc"),
    ("Linux/SSH host", "linux_ssh_host"),
    ("Web device", "web_device"),
]


def upgrade() -> None:
    for label, key in _LABEL_TO_KEY:
        op.execute(
            f"UPDATE devices SET vendor_guess = '{key}' "
            f"WHERE vendor_guess = '{label}'"
        )


def downgrade() -> None:
    for label, key in _LABEL_TO_KEY:
        op.execute(
            f"UPDATE devices SET vendor_guess = '{label}' "
            f"WHERE vendor_guess = '{key}'"
        )
