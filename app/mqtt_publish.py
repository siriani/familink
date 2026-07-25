"""Publishes device/person presence to plain MQTT topics -- this module
knows nothing about Home Assistant or any other specific consumer. It
publishes a retained JSON payload to `{prefix}/<object_id>/state` for
each opted-in device/person; what (if anything) subscribes to that --
Home Assistant's generic MQTT integration configured by hand, a script,
nothing at all -- is entirely up to whoever's running familink. No
`homeassistant/...` discovery topics, no HA-specific payload schema
(device_class, payload_on/off, etc). Runs once per discovery cycle
(app/sync.py), right after devices are refreshed -- publishing is cheap
and idempotent, so re-publishing every cycle even when nothing changed
is fine.

Entirely opt-in at two levels: no broker configured at /settings (see
app/settings.py) = publish_all() is a no-op; a device/person with
mqtt_enabled=False (the default -- see app/models.py) is simply skipped,
even with a broker configured.

Publishes a bridge availability topic ({prefix}/bridge/state, "online"
while this process is connected, "offline" as the client's Last Will) so
a subscriber can tell familink itself is down, independent of any one
topic's state going stale -- the usual pattern for an MQTT bridge/
integration, same idea as Zigbee2MQTT's bridge/state topic. Generic MQTT
convention, not an HA-specific mechanism.

Turning mqtt_enabled off doesn't just stop future updates -- the retained
state topic would otherwise sit on the broker forever showing the last
value. device_mqtt_state/user_mqtt_state track which rows currently have
a retained topic published, so publish_all() can clear it (empty retained
payload) the moment a row is no longer enabled.

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

logger = logging.getLogger("familink.mqtt")

_SLUG_RE = re.compile(r"[^a-z0-9_]+")

_AVAILABILITY_SUFFIX = "bridge/state"


def _object_id(*parts: str) -> str:
    return "familink_" + "_".join(_SLUG_RE.sub("_", p.lower()) for p in parts)


@dataclass
class _MqttConfig:
    host: str
    port: int
    user: str
    password: str
    topic_prefix: str


def _read_config() -> _MqttConfig:
    from app.db import session_scope
    from app.settings import get_setting

    with session_scope() as session:
        return _MqttConfig(
            host=get_setting(session, "mqtt_host") or "",
            port=int(get_setting(session, "mqtt_port") or "1883"),
            user=get_setting(session, "mqtt_user") or "",
            password=get_setting(session, "mqtt_password") or "",
            topic_prefix=get_setting(session, "mqtt_topic_prefix") or "familink",
        )


@dataclass
class _DeviceRow:
    id: int
    mac: str
    hostname: str | None
    current_ip: str | None
    is_online: bool
    vendor_guess: str | None
    fingerbank_manufacturer: str | None
    fingerbank_device_name: str | None
    last_seen: datetime


@dataclass
class _UserRow:
    id: int
    name: str
    is_online: bool
    devices_online: int


def _load_rows() -> tuple[list[_DeviceRow], list[_UserRow], list[tuple[int, str]], list[tuple[int, str]]]:
    """Returns (devices, users, stale_devices, stale_users) -- the last
    two are (id, object_id) pairs for rows that have a retained state
    topic on the broker (a device_mqtt_state/user_mqtt_state row) but
    are no longer mqtt_enabled, so publish_all() can clear them.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.db import session_scope
    from app.models import Device, DeviceMqttState, User, UserMqttState

    with session_scope() as session:
        devices = [
            _DeviceRow(
                id=d.id,
                mac=d.mac,
                hostname=d.hostname,
                current_ip=d.current_ip,
                is_online=d.is_online,
                vendor_guess=d.vendor_guess,
                fingerbank_manufacturer=d.fingerbank_manufacturer,
                fingerbank_device_name=d.fingerbank_device_name,
                last_seen=d.last_seen,
            )
            for d in session.scalars(select(Device).where(Device.mqtt_enabled.is_(True)))
        ]
        device_states = {s.device_id: s for s in session.scalars(select(DeviceMqttState))}
        enabled_device_ids = {row.id for row in devices}
        stale_devices = [
            (device_id, state.object_id)
            for device_id, state in device_states.items()
            if device_id not in enabled_device_ids and state.object_id
        ]

        users = [
            _UserRow(
                id=u.id,
                name=u.name,
                is_online=any(d.is_online for d in u.devices),
                devices_online=sum(1 for d in u.devices if d.is_online),
            )
            for u in session.scalars(
                select(User).where(User.mqtt_enabled.is_(True)).options(selectinload(User.devices))
            )
        ]
        user_states = {s.user_id: s for s in session.scalars(select(UserMqttState))}
        enabled_user_ids = {row.id for row in users}
        stale_users = [
            (user_id, state.object_id)
            for user_id, state in user_states.items()
            if user_id not in enabled_user_ids and state.object_id
        ]
    return devices, users, stale_devices, stale_users


def _save_published(published_device_state: dict[int, str], published_user_state: dict[int, str]) -> None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import DeviceMqttState, UserMqttState

    if not published_device_state and not published_user_state:
        return
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        existing_devices = {s.device_id: s for s in session.scalars(select(DeviceMqttState))}
        for device_id, object_id in published_device_state.items():
            state = existing_devices.get(device_id)
            if state is None:
                state = DeviceMqttState(device_id=device_id)
                session.add(state)
            state.object_id = object_id
            state.last_state_published_at = now

        existing_users = {s.user_id: s for s in session.scalars(select(UserMqttState))}
        for user_id, object_id in published_user_state.items():
            state = existing_users.get(user_id)
            if state is None:
                state = UserMqttState(user_id=user_id)
                session.add(state)
            state.object_id = object_id
            state.last_state_published_at = now

        session.commit()


def _clear_stale(cleared_device_ids: set[int], cleared_user_ids: set[int]) -> None:
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models import DeviceMqttState, UserMqttState

    if not cleared_device_ids and not cleared_user_ids:
        return
    with session_scope() as session:
        if cleared_device_ids:
            session.execute(
                delete(DeviceMqttState).where(DeviceMqttState.device_id.in_(cleared_device_ids))
            )
        if cleared_user_ids:
            session.execute(
                delete(UserMqttState).where(UserMqttState.user_id.in_(cleared_user_ids))
            )
        session.commit()


def _device_payload(row: _DeviceRow) -> dict:
    return {
        "online": row.is_online,
        "mac": row.mac,
        "hostname": row.hostname,
        "ip": row.current_ip,
        "manufacturer": row.fingerbank_manufacturer,
        "model": row.fingerbank_device_name or row.vendor_guess,
        "last_seen": row.last_seen.isoformat(),
    }


def _user_payload(row: _UserRow) -> dict:
    return {
        "online": row.is_online,
        "name": row.name,
        "devices_online": row.devices_online,
    }


async def publish_all() -> None:
    config = await asyncio.to_thread(_read_config)
    if not config.host:
        return

    try:
        import aiomqtt
    except ImportError:
        logger.error("MQTT host is configured but aiomqtt isn't installed — check the Dockerfile")
        return

    devices, users, stale_devices, stale_users = await asyncio.to_thread(_load_rows)
    if not devices and not users and not stale_devices and not stale_users:
        return

    availability_topic = f"{config.topic_prefix}/{_AVAILABILITY_SUFFIX}"

    published_device_state: dict[int, str] = {}
    published_user_state: dict[int, str] = {}
    cleared_device_ids: set[int] = set()
    cleared_user_ids: set[int] = set()
    try:
        async with aiomqtt.Client(
            hostname=config.host,
            port=config.port,
            username=config.user or None,
            password=config.password or None,
            will=aiomqtt.Will(topic=availability_topic, payload="offline", retain=True),
        ) as client:
            await client.publish(availability_topic, "online", retain=True)

            for row in devices:
                object_id = _object_id(row.mac)
                state_topic = f"{config.topic_prefix}/{object_id}/state"
                await client.publish(state_topic, json.dumps(_device_payload(row)), retain=True)
                published_device_state[row.id] = object_id

            for row in users:
                object_id = _object_id("user", str(row.id))
                state_topic = f"{config.topic_prefix}/{object_id}/state"
                await client.publish(state_topic, json.dumps(_user_payload(row)), retain=True)
                published_user_state[row.id] = object_id

            # No-longer-enabled rows that still have a retained state
            # topic on the broker -- an empty retained payload clears it,
            # instead of leaving it frozen on its last value forever.
            for device_id, object_id in stale_devices:
                await client.publish(f"{config.topic_prefix}/{object_id}/state", "", retain=True)
                cleared_device_ids.add(device_id)
            for user_id, object_id in stale_users:
                await client.publish(f"{config.topic_prefix}/{object_id}/state", "", retain=True)
                cleared_user_ids.add(user_id)
    except Exception:
        logger.exception("mqtt publish cycle failed")
        # Still persist whatever succeeded before the failure -- partial
        # progress beats re-sending every topic next cycle.

    if published_device_state or published_user_state:
        await asyncio.to_thread(_save_published, published_device_state, published_user_state)
        logger.info(
            "mqtt: published %d device(s), %d user(s)",
            len(published_device_state),
            len(published_user_state),
        )
    if cleared_device_ids or cleared_user_ids:
        await asyncio.to_thread(_clear_stale, cleared_device_ids, cleared_user_ids)
        logger.info(
            "mqtt: cleared %d disabled device(s), %d disabled user(s)",
            len(cleared_device_ids),
            len(cleared_user_ids),
        )
