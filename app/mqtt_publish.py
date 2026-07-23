"""Publishes device online/offline presence to MQTT using Home Assistant's
MQTT Discovery convention, so devices show up in HA automatically as
`binary_sensor` (device_class connectivity) entities. Runs once per
discovery cycle (app/sync.py), right after devices are refreshed —
publishing state is cheap and idempotent, so re-publishing every cycle
even when nothing changed is fine.

Entirely opt-in: if MQTT_HOST is empty, publish_all() is a no-op.

DB access is split from the MQTT I/O (same asyncio.to_thread pattern as
app/sync.py) rather than mixing sync SQLAlchemy calls into the async
publish loop directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import MQTT_HOST, MQTT_PASSWORD, MQTT_PORT, MQTT_TOPIC_PREFIX, MQTT_USER

logger = logging.getLogger("familink.mqtt")

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _object_id(mac: str) -> str:
    return "familink_" + _SLUG_RE.sub("_", mac.lower())


@dataclass
class _DeviceRow:
    id: int
    mac: str
    hostname: str | None
    is_online: bool
    vendor_guess: str | None
    discovery_published: bool


def _load_devices() -> list[_DeviceRow]:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Device, DeviceMqttState

    with session_scope() as session:
        states = {s.device_id: s for s in session.scalars(select(DeviceMqttState))}
        return [
            _DeviceRow(
                id=d.id,
                mac=d.mac,
                hostname=d.hostname,
                is_online=d.is_online,
                vendor_guess=d.vendor_guess,
                discovery_published=states.get(d.id) is not None
                and states[d.id].discovery_published_at is not None,
            )
            for d in session.scalars(select(Device))
        ]


def _save_published(published_discovery_ids: set[int], published_state: dict[int, str]) -> None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import DeviceMqttState

    if not published_state:
        return
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        existing = {s.device_id: s for s in session.scalars(select(DeviceMqttState))}
        for device_id, object_id in published_state.items():
            state = existing.get(device_id)
            if state is None:
                state = DeviceMqttState(device_id=device_id)
                session.add(state)
            state.object_id = object_id
            if device_id in published_discovery_ids:
                state.discovery_published_at = now
            state.last_state_published_at = now
        session.commit()


def _discovery_config(row: _DeviceRow, state_topic: str) -> dict:
    object_id = _object_id(row.mac)
    return {
        "name": row.hostname or row.mac,
        "unique_id": object_id,
        "state_topic": state_topic,
        "device_class": "connectivity",
        "payload_on": "online",
        "payload_off": "offline",
        "device": {
            "identifiers": [object_id],
            "name": row.hostname or row.mac,
            "manufacturer": "familink",
            "model": row.vendor_guess or "network device",
        },
    }


async def publish_all() -> None:
    if not MQTT_HOST:
        return

    try:
        import aiomqtt
    except ImportError:
        logger.error("MQTT_HOST is set but aiomqtt isn't installed — check the Dockerfile")
        return

    devices = await asyncio.to_thread(_load_devices)
    if not devices:
        return

    published_discovery: set[int] = set()
    published_state: dict[int, str] = {}
    try:
        async with aiomqtt.Client(
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            username=MQTT_USER or None,
            password=MQTT_PASSWORD or None,
        ) as client:
            for row in devices:
                object_id = _object_id(row.mac)
                state_topic = f"{MQTT_TOPIC_PREFIX}/{object_id}/state"
                if not row.discovery_published:
                    config_topic = f"homeassistant/binary_sensor/{object_id}/config"
                    payload = json.dumps(_discovery_config(row, state_topic))
                    await client.publish(config_topic, payload, retain=True)
                    published_discovery.add(row.id)
                await client.publish(
                    state_topic, "online" if row.is_online else "offline", retain=True
                )
                published_state[row.id] = object_id
    except Exception:
        logger.exception("mqtt publish cycle failed")
        # Still persist whatever succeeded before the failure -- partial
        # progress beats re-sending every discovery config next cycle.

    if published_state:
        await asyncio.to_thread(_save_published, published_discovery, published_state)
        logger.info(
            "mqtt: published state for %d devices (%d new discovery configs)",
            len(published_state),
            len(published_discovery),
        )
