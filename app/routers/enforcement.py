"""Read-only overview of every device whose MikroTik state doesn't match
its assigned group yet. No bulk-apply here on purpose — each application
is a deliberate per-device click from the device detail page (see
app/routers/devices.py:post_apply_mikrotik). See SPEC.md for why.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.enforcement import pending_action, pending_action_label
from app.models import Device
from app.templating import templates

router = APIRouter()


@router.get("/enforcement", response_class=HTMLResponse)
def page_enforcement(request: Request, db: Session = Depends(get_db)):
    devices = list(db.scalars(select(Device).options(selectinload(Device.scan_results))))
    pending = [
        {"device": d, "action": a, "label": pending_action_label(a)}
        for d in devices
        if (a := pending_action(d)) != "none"
    ]
    pending.sort(key=lambda p: (p["action"], (p["device"].hostname or p["device"].mac).lower()))
    return templates.TemplateResponse(
        request, "enforcement_list.html", {"pending": pending, "total_devices": len(devices)}
    )
