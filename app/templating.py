"""Single shared Jinja2Templates instance so the timezone filter (and any
future template config) only has to be registered once, instead of once
per router.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import DISPLAY_TIMEZONE

_tz = ZoneInfo(DISPLAY_TIMEZONE)


def local_datetime(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Every datetime familink stores is UTC (see app/config.py) — MySQL
    DATETIME columns come back naive (no tzinfo attached), so we assume
    naive == UTC before converting to DISPLAY_TIMEZONE. Never format a
    stored datetime directly with strftime() in a template; always go
    through this filter.
    """
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_tz).strftime(fmt)


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["localtime"] = local_datetime
