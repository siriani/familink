"""Device listing/detail + the one deliberate write path of the foundation
phase: reassigning a device's group or linked user IN THE DATABASE ONLY.
Nothing here calls MikroTik.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Group, User
from app.schemas import DeviceOut, DeviceUpdate

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _get_device_or_404(db: Session, mac: str) -> Device:
    device = db.scalar(select(Device).where(Device.mac == mac.lower()))
    if device is None:
        raise HTTPException(404, f"device '{mac}' not found")
    return device


def _query_devices(db: Session, q: str | None, group_id: int | None) -> list[Device]:
    stmt = select(Device)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Device.hostname.ilike(like),
                Device.mac.ilike(like),
                Device.current_ip.ilike(like),
            )
        )
    if group_id:
        stmt = stmt.where(Device.group_id == group_id)
    devices = list(db.scalars(stmt))
    devices.sort(key=lambda d: (not d.is_online, (d.hostname or d.mac).lower()))
    return devices


# ── JSON API ────────────────────────────────────────────────────────────


@router.get("/api/devices", response_model=list[DeviceOut])
def api_list_devices(
    q: str | None = None, group_id: int | None = None, db: Session = Depends(get_db)
):
    return _query_devices(db, q, group_id)


@router.get("/api/devices/{mac}", response_model=DeviceOut)
def api_get_device(mac: str, db: Session = Depends(get_db)):
    return _get_device_or_404(db, mac)


@router.patch("/api/devices/{mac}", response_model=DeviceOut)
def api_update_device(mac: str, body: DeviceUpdate, db: Session = Depends(get_db)):
    device = _get_device_or_404(db, mac)
    fields = body.model_dump(exclude_unset=True)
    if "group_id" in fields and fields["group_id"] is not None:
        if db.get(Group, fields["group_id"]) is None:
            raise HTTPException(422, f"group_id {fields['group_id']} does not exist")
        device.group_id = fields["group_id"]
    if "user_id" in fields:
        if fields["user_id"] is not None and db.get(User, fields["user_id"]) is None:
            raise HTTPException(422, f"user_id {fields['user_id']} does not exist")
        device.user_id = fields["user_id"]
    if "notes" in fields:
        device.notes = fields["notes"]
    db.commit()
    db.refresh(device)
    return device


# ── HTML admin panel ───────────────────────────────────────────────────


@router.get("/devices", response_class=HTMLResponse)
def page_list_devices(
    request: Request, q: str | None = None, group_id: int | None = None, db: Session = Depends(get_db)
):
    devices = _query_devices(db, q, group_id)
    groups = list(db.scalars(select(Group)))
    return templates.TemplateResponse(
        request,
        "devices_list.html",
        {"devices": devices, "groups": groups, "q": q or "", "group_id": group_id},
    )


@router.get("/devices/{mac}", response_class=HTMLResponse)
def page_device_detail(request: Request, mac: str, db: Session = Depends(get_db)):
    device = _get_device_or_404(db, mac)
    groups = list(db.scalars(select(Group)))
    users = list(db.scalars(select(User).order_by(User.name)))
    return templates.TemplateResponse(
        request,
        "device_detail.html",
        {"device": device, "groups": groups, "users": users},
    )


@router.post("/devices/{mac}/group")
def post_device_group(mac: str, group_id: int = Form(...), db: Session = Depends(get_db)):
    device = _get_device_or_404(db, mac)
    if db.get(Group, group_id) is None:
        raise HTTPException(422, f"group_id {group_id} does not exist")
    device.group_id = group_id
    db.commit()
    return RedirectResponse(f"/devices/{mac}", status_code=303)


@router.post("/devices/{mac}/user")
def post_device_user(mac: str, user_id: str = Form(""), db: Session = Depends(get_db)):
    device = _get_device_or_404(db, mac)
    device.user_id = int(user_id) if user_id else None
    db.commit()
    return RedirectResponse(f"/devices/{mac}", status_code=303)


@router.post("/devices/{mac}/notes")
def post_device_notes(mac: str, notes: str = Form(""), db: Session = Depends(get_db)):
    device = _get_device_or_404(db, mac)
    device.notes = notes or None
    db.commit()
    return RedirectResponse(f"/devices/{mac}", status_code=303)
