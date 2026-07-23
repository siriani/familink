"""Pydantic request/response models for the JSON API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    hotspot_required: bool
    description: str | None
    is_default: bool
    daily_limit_weekday_s: int | None
    daily_limit_weekend_s: int | None
    night_block_start: str | None
    night_block_end: str | None
    night_block_address_list: str | None


class GroupWithCount(GroupOut):
    device_count: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
    birthdate: date | None
    daily_limit_weekday_s: int | None
    daily_limit_weekend_s: int | None
    seconds_used_today: int
    blocked: bool


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mac: str
    current_ip: str | None
    hostname: str | None
    vendor_guess: str | None
    first_seen: datetime
    last_seen: datetime
    is_online: bool
    mikrotik_bound: bool
    mikrotik_bypassed: bool
    notes: str | None
    user: UserOut | None
    group: GroupOut


class DeviceUpdate(BaseModel):
    group_id: int | None = None
    user_id: int | None = None
    notes: str | None = None


class HealthOut(BaseModel):
    status: str
    devices_total: int
    last_sync_ago_s: float | None
