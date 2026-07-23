"""SQLAlchemy 2.0 models — the full data shape for familink, including
tables for phases that aren't implemented yet (see SPEC.md § Roadmap).
Those stub tables have no service code touching them; they exist so a
future phase never needs a schema-breaking migration.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Group(Base):
    """Access class for a device: does it have to pass through the MikroTik
    hotspot (restricted, quota-able) or is it free (liberado)? Seeded with
    exactly two rows by the initial migration — see migrations/versions/
    0001_initial_schema.py. Only one group may have is_default=True; new
    devices discovered by the sync loop land there.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hotspot_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    """A family member / registered person. NOT an app-login account — no
    password here. A future captive-portal auth phase needs its own
    mechanism (session token, magic link, whatever) rather than bolting a
    password onto this table.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    birthdate: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    devices: Mapped[list["Device"]] = relationship(back_populates="user")


class Device(Base):
    """One row per MAC address seen on the network. `current_ip`,
    `hostname`, `is_online`, `mikrotik_bound`, `mikrotik_bypassed`, and
    `last_seen` are owned by the sync loop (app/sync.py) and overwritten
    every cycle. `group_id`, `user_id`, and `notes` are admin-owned — the
    sync loop must never touch them once a device exists.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
    current_ip: Mapped[str | None] = mapped_column(String(45))
    hostname: Mapped[str | None] = mapped_column(String(255))
    vendor_guess: Mapped[str | None] = mapped_column(String(100))
    first_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mikrotik_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mikrotik_bypassed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User | None"] = relationship(back_populates="devices")
    group: Mapped["Group"] = relationship()
    scan_results: Mapped[list["DeviceScanResult"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class DeviceScanResult(Base):
    """Stub for the future nmap-based port scanner (see SPEC.md). No code
    writes here yet in the foundation phase.
    """

    __tablename__ = "device_scan_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(10), nullable=False, default="tcp")
    service_guess: Mapped[str | None] = mapped_column(String(100))
    banner: Mapped[str | None] = mapped_column(Text)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    device: Mapped["Device"] = relationship(back_populates="scan_results")


class DeviceMqttState(Base):
    """Stub for the future MQTT / Home Assistant discovery publisher (see
    SPEC.md). Tracks whether a device's HA discovery config has already been
    published, so the publisher doesn't resend it every cycle. No code
    writes here yet.
    """

    __tablename__ = "device_mqtt_state"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    object_id: Mapped[str | None] = mapped_column(String(100))
    discovery_published_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_state_published_at: Mapped[datetime | None] = mapped_column(DateTime)


class RegistrationToken(Base):
    """Stub for the future captive-portal self-registration flow (see
    SPEC.md). `device_mac` is nullable because a token may be issued before
    the connecting device's MAC is known. No code writes here yet.
    """

    __tablename__ = "registration_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    device_mac: Mapped[str | None] = mapped_column(String(17))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
