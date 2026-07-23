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

# First (ports, label) whose intersection with the open-port set is
# non-empty wins -- order matters, most specific first.
_TYPE_HINTS: list[tuple[set[int], str]] = [
    ({34567}, "DVR/NVR (XM/Xiongmai-style)"),
    ({37777}, "DVR/NVR (Dahua-compatible)"),
    ({8899}, "Camera (ONVIF)"),
    ({554}, "Camera/streaming (RTSP)"),
    ({9100}, "Printer (JetDirect)"),
    ({631}, "Printer (IPP)"),
    ({8123}, "Home Assistant"),
    ({1883}, "MQTT broker"),
    ({3306}, "MySQL/MariaDB"),
    ({5432}, "PostgreSQL"),
    ({5900}, "VNC"),
    ({22}, "Linux/SSH host"),
    ({80, 443, 8080, 8081, 8000, 8443, 8880}, "Web device"),
]

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
    for ports, label in _TYPE_HINTS:
        if ports & open_ports:
            return label
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
