"""nmap-based port scanner, triggered automatically the first time the
discovery loop (app/sync.py) sees a brand new device — never re-triggered
on its own afterward, and there's also a manual "Rescan" button on the
device detail page (POST /devices/{mac}/scan) for anyone who wants a
fresh read later. Read-only against the scanned device (a TCP connect
scan, nothing MikroTik-facing) — writes only to this app's own DB.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger("familink.portscan")

# A curated list, not nmap's default top-1000 -- fast (~1-2s/device) and
# covers what's actually shown up on this kind of home network: cameras
# (554/8899/34567/37777), printers (9100/631), IoT web UIs (80/443/8080),
# common services (22/1883/3306/8123...). Extend as new device types
# surface rather than switching to a slow full scan.
PORTS = (
    "21,22,23,25,53,80,110,143,161,443,554,631,993,995,1883,3306,5000,"
    "5432,5900,8000,8008,8080,8081,8123,8443,8899,8880,9100,9200,34567,37777"
)

SCAN_TIMEOUT_S = 30.0

# First (ports, key) whose intersection with the open-port set is
# non-empty wins -- order matters, most specific first. `device.vendor_guess`
# stores the KEY (stable, language-invariant), never the label -- the
# label is looked up + translated at display time via TYPE_LABEL_MSGIDS
# (see app/routers/devices.py). This split exists because vendor_guess is
# a persisted current-state field (unlike e.g. EnforcementLog.detail, an
# audit trail that's an explicit i18n non-goal) -- storing literal English
# text here would make it impossible to re-translate existing rows without
# a migration (see migrations/versions/0005_vendor_guess_keys.py, which
# did exactly that migration once, the last time this was literal text).
_TYPE_HINTS: list[tuple[set[int], str]] = [
    ({34567}, "dvr_nvr_xm"),
    ({37777}, "dvr_nvr_dahua"),
    ({8899}, "camera_onvif"),
    ({554}, "camera_rtsp"),
    ({9100}, "printer_jetdirect"),
    ({631}, "printer_ipp"),
    ({8123}, "home_assistant"),
    ({1883}, "mqtt_broker"),
    ({3306}, "mysql_mariadb"),
    ({5432}, "postgresql"),
    ({5900}, "vnc"),
    ({22}, "linux_ssh_host"),
    ({80, 443, 8080, 8081, 8000, 8443, 8880}, "web_device"),
]

# pybabel extract only finds literal string arguments, not variables --
# these msgids get discovered via app/enforcement.py's _extraction_hints()
# stub instead (kept there, next to pending_action_label's equivalent
# extraction hint, rather than duplicated here).
TYPE_LABEL_MSGIDS: dict[str, str] = {
    "dvr_nvr_xm": "DVR/NVR (XM/Xiongmai-style)",
    "dvr_nvr_dahua": "DVR/NVR (Dahua-compatible)",
    "camera_onvif": "Camera (ONVIF)",
    "camera_rtsp": "Camera/streaming (RTSP)",
    "printer_jetdirect": "Printer (JetDirect)",
    "printer_ipp": "Printer (IPP)",
    "home_assistant": "Home Assistant",
    "mqtt_broker": "MQTT broker",
    "mysql_mariadb": "MySQL/MariaDB",
    "postgresql": "PostgreSQL",
    "vnc": "VNC",
    "linux_ssh_host": "Linux/SSH host",
    "web_device": "Web device",
}

# Only ports with a browser-openable standard scheme get a clickable link
# on the device detail page (app/templating.py registers this as the
# `port_url` Jinja global). Raw/proprietary protocols (XM DVRIP on 34567,
# Dahua on 37777, MQTT, MySQL, printer raw socket on 9100...) have no
# sensible URL to hand a browser, so they stay plain text.
_PORT_URL_SCHEMES: dict[int, str] = {
    21: "ftp://{ip}",
    22: "ssh://{ip}",
    23: "telnet://{ip}",
    80: "http://{ip}",
    443: "https://{ip}",
    554: "rtsp://{ip}",
    631: "http://{ip}:631",
    5900: "vnc://{ip}",
    8000: "http://{ip}:8000",
    8008: "http://{ip}:8008",
    8080: "http://{ip}:8080",
    8081: "http://{ip}:8081",
    8123: "http://{ip}:8123",
    8443: "https://{ip}:8443",
    8880: "http://{ip}:8880",
    9200: "http://{ip}:9200",
}


def guess_port_url(ip: str, port: int) -> str | None:
    template = _PORT_URL_SCHEMES.get(port)
    return template.format(ip=ip) if template else None


_GREP_PORTS_RE = re.compile(r"Ports: (.+?)(?:\tIgnored|\n|$)")


def _parse_greppable(text: str) -> list[tuple[int, str]]:
    match = _GREP_PORTS_RE.search(text)
    if not match:
        return []
    open_ports = []
    for entry in match.group(1).split(", "):
        parts = entry.strip().split("/")
        if len(parts) >= 5 and parts[1] == "open":
            try:
                port = int(parts[0])
            except ValueError:
                continue
            open_ports.append((port, parts[4] or ""))
    return open_ports


def guess_type(open_ports: set[int]) -> str | None:
    """Returns a stable key into TYPE_LABEL_MSGIDS (e.g. "camera_onvif"),
    not a display label -- translate at render time, see
    app/routers/devices.py."""
    for ports, key in _TYPE_HINTS:
        if ports & open_ports:
            return key
    return None


async def scan_ports(ip: str, timeout: float = SCAN_TIMEOUT_S) -> list[tuple[int, str]]:
    """TCP connect scan (-sT, no special privileges needed in a container)
    against a curated port list. Returns [] on any failure -- a scan that
    can't complete is not worth crashing anything over.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "nmap", "-Pn", "-sT", "-T4", "--open", "-p", PORTS, "-oG", "-", ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("nmap not found — is it installed in the image? (see Dockerfile)")
        return []
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("scan of %s timed out after %.0fs", ip, timeout)
        return []
    return _parse_greppable(stdout.decode(errors="replace"))


def _store_results(device_id: int, results: list[tuple[int, str]]) -> None:
    from app.db import session_scope
    from app.models import Device, DeviceScanResult

    with session_scope() as session:
        device = session.get(Device, device_id)
        if device is None:
            return  # device was deleted between scan trigger and completion
        now = datetime.now(timezone.utc)
        for port, service in results:
            session.add(
                DeviceScanResult(
                    device_id=device_id,
                    port=port,
                    protocol="tcp",
                    service_guess=service or None,
                    scanned_at=now,
                )
            )
        if results:
            guess = guess_type({p for p, _ in results})
            if guess and not device.vendor_guess:
                device.vendor_guess = guess
        session.commit()


async def scan_and_store(device_id: int, ip: str) -> None:
    logger.info("scanning new device id=%s ip=%s", device_id, ip)
    results = await scan_ports(ip)
    await asyncio.to_thread(_store_results, device_id, results)
    logger.info("scan of %s complete: %d open ports", ip, len(results))
