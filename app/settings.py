"""Reads/writes app/models.py:Setting -- see that model's docstring for
why this exists instead of another env var.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Setting


def get_setting(db: Session, key: str) -> str | None:
    setting = db.get(Setting, key)
    return setting.value if setting else None


def set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(Setting, key)
    if setting is None:
        setting = Setting(key=key)
        db.add(setting)
    setting.value = value
    db.commit()
