"""Single shared Jinja2Templates instance so the timezone filter (and any
future template config) only has to be registered once, instead of once
per router.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from babel.numbers import format_decimal
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.config import DISPLAY_TIMEZONE
from app.i18n import DEFAULT_LOCALE, i18n_context_processor
from app.portscan import guess_port_url

_tz = ZoneInfo(DISPLAY_TIMEZONE)

# familink locale tag -> Babel locale identifier (mostly identical, except
# Babel wants an underscore for pt-BR and doesn't know the "-Hans" suffix).
_BABEL_LOCALE = {
    "pt-BR": "pt_BR",
    "en": "en",
    "es": "es",
    "de": "de",
    "zh-Hans": "zh_Hans",
}


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


@pass_context
def local_number(ctx, value, decimals: int = 1) -> str:
    """Locale-aware decimal formatting -- e.g. 2.5 renders as "2,5" in
    pt-BR/de, "2.5" in en. Reads `lang` off the same per-request context
    dict app.i18n.i18n_context_processor already populates, so this has
    the identical concurrency-safety story as `_()`/`ngettext()` (see
    app/i18n.py's module docstring)."""
    locale = _BABEL_LOCALE.get(ctx.get("lang", DEFAULT_LOCALE), "en")
    fmt = "0." + "0" * decimals if decimals else "0"
    return format_decimal(round(value, decimals), format=fmt, locale=locale)


# context_processors: each one runs fresh per TemplateResponse() call with
# that call's own Request (starlette.templating.Jinja2Templates merges the
# result into a per-call context dict, never touching the shared
# Environment) -- this is what makes per-request locale safe under
# concurrent requests in different languages. See app/i18n.py.
templates = Jinja2Templates(
    directory="app/templates", context_processors=[i18n_context_processor]
)
templates.env.filters["localtime"] = local_datetime
templates.env.filters["localnumber"] = local_number
templates.env.globals["port_url"] = guess_port_url
