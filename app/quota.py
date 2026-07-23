"""Quota accounting -- familink's own, no MikroTik-side counter involved.

todays_limit_s()/applicable_group() are pure. tick_and_enforce() is the
per-discovery-cycle job: for every user with a quota, add SYNC_INTERVAL_S
to seconds_used_today if any of their devices is online right now, and the
moment the total reaches the limit, block every linked device on MikroTik
(app/mikrotik_quota.py:block_device) -- a plain "cut them off", no login/
session concept. run_nightly_reset() zeroes the counter and unblocks for
the new day, once daily.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import DISPLAY_TIMEZONE, SYNC_INTERVAL_S
from app.models import Group, User

logger = logging.getLogger("familink.quota")

_tz = ZoneInfo(DISPLAY_TIMEZONE)


def today_is_weekend() -> bool:
    """Uses DISPLAY_TIMEZONE's current date, not the server's/UTC's --
    this exact mismatch (checking the wrong timezone's weekday) is what
    silently broke the original MikroTik quota script for months.
    """
    return datetime.now(_tz).weekday() >= 5  # Mon=0 .. Sun=6


def applicable_group(user: User) -> Group | None:
    """First device (by id) linked to this user whose group has a quota
    default set. One person, one quota policy -- deliberately doesn't try
    to blend multiple groups if someone's devices are split across them.
    """
    for device in sorted(user.devices, key=lambda d: d.id):
        group = device.group
        if group.daily_limit_weekday_s is not None or group.daily_limit_weekend_s is not None:
            return group
    return None


def todays_limit_s(user: User) -> int | None:
    """User's own override wins when set; otherwise falls back to
    applicable_group(user)'s default for today (weekday vs weekend).
    None means "no quota applies" (tick_and_enforce skips this user).
    """
    weekend = today_is_weekend()

    user_limit = user.daily_limit_weekend_s if weekend else user.daily_limit_weekday_s
    if user_limit is not None:
        return user_limit

    group = applicable_group(user)
    if group is None:
        return None
    return group.daily_limit_weekend_s if weekend else group.daily_limit_weekday_s


def is_user_online(user: User) -> bool:
    return any(device.is_online for device in user.devices)


# ── DB helpers (sync, run via asyncio.to_thread from the async loop) ──────


def _load_quota_users() -> list[User]:
    from sqlalchemy import select

    from app.db import session_scope

    with session_scope() as session:
        users = list(session.scalars(select(User)))
        for user in users:
            for device in user.devices:
                _ = device.group  # load while session is open, see below
        return users


def _save_tick_results(
    ticked: dict[int, int], newly_blocked: set[int], unblocked: set[int]
) -> None:
    """ticked: {user_id: new_seconds_used_today}. newly_blocked/unblocked:
    user_ids whose `blocked` flag should flip. One commit for the whole
    cycle.
    """
    from sqlalchemy import select

    from app.db import session_scope

    if not ticked and not newly_blocked and not unblocked:
        return
    with session_scope() as session:
        ids = set(ticked) | newly_blocked | unblocked
        users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(ids)))}
        for user_id, seconds in ticked.items():
            users[user_id].seconds_used_today = seconds
        for user_id in newly_blocked:
            users[user_id].blocked = True
        for user_id in unblocked:
            users[user_id].blocked = False
        session.commit()


def _log_quota_event(user_id: int, limit_s: int | None, success: bool, detail: str) -> None:
    from app.db import session_scope
    from app.models import QuotaLog

    with session_scope() as session:
        session.add(QuotaLog(user_id=user_id, limit_s=limit_s, success=success, detail=detail))
        session.commit()


def _reset_all_quota_users() -> list[User]:
    """Nightly: zero every quota user's counter and clear `blocked`.
    Returns the users that WERE blocked (those are the only ones that
    need an actual MikroTik unblock call).
    """
    from sqlalchemy import select

    from app.db import session_scope

    with session_scope() as session:
        users = list(session.scalars(select(User).where(User.seconds_used_today > 0)))
        was_blocked = [u for u in users if u.blocked]
        for user in was_blocked:
            for device in user.devices:
                _ = device.mac  # load while session is open
        for user in session.scalars(select(User)):
            user.seconds_used_today = 0
            user.blocked = False
        session.commit()
        return was_blocked


# ── Per-cycle tick + enforce ────────────────────────────────────────────


async def tick_and_enforce(interval_s: float = SYNC_INTERVAL_S) -> None:
    from app.mikrotik_quota import block_device
    from app.sync import get_mikrotik_client

    users = await asyncio.to_thread(_load_quota_users)
    ticked: dict[int, int] = {}
    newly_blocked: set[int] = set()
    client = None

    for user in users:
        if user.blocked:
            continue  # already cut off, nothing to do until nightly reset
        limit_s = todays_limit_s(user)
        if limit_s is None or not is_user_online(user):
            continue
        new_total = min(user.seconds_used_today + int(interval_s), limit_s)
        ticked[user.id] = new_total
        if new_total >= limit_s:
            client = client or get_mikrotik_client()
            all_ok = True
            details = []
            for device in user.devices:
                result = await block_device(client, device)
                details.append(f"{device.mac}: {result.detail}")
                if not result.success:
                    all_ok = False
                    logger.warning(
                        "failed to block device %s for user '%s': %s",
                        device.mac, user.name, result.detail,
                    )
            newly_blocked.add(user.id)
            await asyncio.to_thread(
                _log_quota_event, user.id, limit_s, all_ok, "blocked: " + "; ".join(details)
            )
            logger.info("user '%s' hit their daily quota (%ss) -- blocked", user.name, limit_s)

    await asyncio.to_thread(_save_tick_results, ticked, newly_blocked, set())


# ── Nightly reset ───────────────────────────────────────────────────────


def _seconds_until_next_reset() -> float:
    now = datetime.now(_tz)
    target = now.replace(hour=0, minute=1, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_nightly_reset() -> None:
    from app.mikrotik_quota import unblock_device
    from app.sync import get_mikrotik_client

    was_blocked = await asyncio.to_thread(_reset_all_quota_users)
    if not was_blocked:
        return
    client = get_mikrotik_client()
    for user in was_blocked:
        all_ok = True
        details = []
        for device in user.devices:
            result = await unblock_device(client, device)
            details.append(f"{device.mac}: {result.detail}")
            if not result.success:
                all_ok = False
                logger.warning(
                    "failed to unblock device %s for user '%s': %s",
                    device.mac, user.name, result.detail,
                )
        await asyncio.to_thread(
            _log_quota_event, user.id, None, all_ok, "unblocked (nightly reset): " + "; ".join(details)
        )
    logger.info("nightly quota reset: unblocked %d users", len(was_blocked))


async def nightly_reset_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_next_reset())
        try:
            await run_nightly_reset()
        except Exception:
            logger.exception("nightly quota reset cycle failed")
