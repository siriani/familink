"""No-auth health endpoint, cheap enough to poll every minute (Uptime Kuma
style) without touching MikroTik.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import sync
from app.db import get_db
from app.models import Device
from app.schemas import HealthOut

router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)):
    devices_total = db.scalar(select(func.count(Device.id))) or 0
    return HealthOut(
        status="ok",
        devices_total=devices_total,
        last_sync_ago_s=sync.last_cycle_age_s(),
    )
