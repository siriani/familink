"""Locale resolution + translation loading for the admin panel and the
unauthenticated /captive page. Two different resolution strategies on
purpose (see LocaleMiddleware) -- the admin panel is the same person/family
visiting repeatedly (cookie + manual switcher makes sense), /captive is any
visitor on their own device (Accept-Language detection makes sense, no
login/cookie to hang state off of).

Templates call `_()`/`ngettext()`/`pgettext()` as plain Jinja2 context
variables (see app/templating.py's context_processors), not the
`{% trans %}` tag / `jinja2.ext.i18n` extension -- that extension's
"newstyle gettext" support relies on private Jinja internals
(`jinja2.ext._make_new_gettext`), which is exactly the kind of fragile
cleverness this project avoids elsewhere. Plain context variables are fully
public, per-request-safe (see app/templating.py's context_processors
mechanism), and still correctly discovered by `pybabel extract`'s Jinja2
scanner.
"""
from __future__ import annotations

import gettext
import logging
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import DISPLAY_LANGUAGE

logger = logging.getLogger("familink.i18n")

# Order = fallback preference if Accept-Language matches nothing exactly.
SUPPORTED_LOCALES = ["pt-BR", "en", "es", "de", "zh-Hans"]

# familink locale tag -> gettext/Babel directory name (POSIX-style
# underscore, not the BCP-47 hyphen).
_GETTEXT_DIR = {
    "pt-BR": "pt_BR",
    "en": "en",
    "es": "es",
    "de": "de",
    "zh-Hans": "zh_Hans",
}

_LOCALEDIR = Path(__file__).parent / "locales"

DEFAULT_LOCALE = DISPLAY_LANGUAGE if DISPLAY_LANGUAGE in SUPPORTED_LOCALES else "en"
if DEFAULT_LOCALE != DISPLAY_LANGUAGE:
    logger.warning(
        "DISPLAY_LANGUAGE=%r is not one of %s -- falling back to 'en'",
        DISPLAY_LANGUAGE, SUPPORTED_LOCALES,
    )

# Loaded once at import time -- fallback=True means a locale whose .mo
# hasn't been compiled yet (e.g. a contributor edited a .po but forgot
# `pybabel compile`) degrades to English passthrough instead of raising.
_translations: dict[str, gettext.NullTranslations] = {
    loc: gettext.translation("familink", _LOCALEDIR, languages=[dirname], fallback=True)
    for loc, dirname in _GETTEXT_DIR.items()
}


def get_translations(locale: str) -> gettext.NullTranslations:
    return _translations.get(locale, _translations[DEFAULT_LOCALE])


def warn_if_translations_missing() -> None:
    """Called once from main.py's lifespan -- same pattern as
    app.auth.warn_if_auth_disabled(). A missing .mo isn't fatal
    (fallback=True), just silently shows English, so this is the only
    signal an operator gets that a locale's compile step didn't run.
    """
    for loc, dirname in _GETTEXT_DIR.items():
        mo_path = _LOCALEDIR / dirname / "LC_MESSAGES" / "familink.mo"
        if not mo_path.exists():
            logger.warning(
                "no compiled translations for %r (expected %s) -- "
                "that locale will show English until `pybabel compile` runs",
                loc, mo_path,
            )


def parse_accept_language(header: str) -> list[str]:
    """Real weighted-list parsing, e.g. "en-US,en;q=0.9,pt;q=0.8" ->
    ["en-US", "en", "pt"] ordered by descending q (ties keep header order,
    since Python's sort is stable)."""
    tagged: list[tuple[str, float]] = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        tag, _, qpart = part.partition(";")
        tag = tag.strip()
        if not tag:
            continue
        q = 1.0
        qpart = qpart.strip()
        if qpart.startswith("q="):
            try:
                q = float(qpart[2:])
            except ValueError:
                q = 1.0
        tagged.append((tag, q))
    tagged.sort(key=lambda pair: pair[1], reverse=True)
    return [tag for tag, _ in tagged]


def match_accept_language(header: str, supported: list[str], default: str) -> str:
    """First exact tag match wins; then primary-subtag match (e.g. "pt-PT"
    matches "pt-BR" since neither is a better fit than the other, and
    "en-US" matches plain "en"). Any zh-* variant (including Traditional
    zh-TW/zh-HK) maps to zh-Hans since that's the only Chinese variant
    shipped -- a known, accepted gap for Traditional-Chinese visitors.
    """
    for tag in parse_accept_language(header):
        tag_l = tag.lower()
        for loc in supported:
            if loc.lower() == tag_l:
                return loc
        primary = tag_l.split("-")[0]
        for loc in supported:
            loc_primary = loc.lower().split("-")[0]
            if loc_primary == primary or (primary == "zh" and loc == "zh-Hans"):
                return loc
    return default


LOCALE_COOKIE = "familink_lang"
_CAPTIVE_PATH = "/captive"


class LocaleMiddleware(BaseHTTPMiddleware):
    """Resolves request.state.locale before any router/template code runs.
    Registered in main.py AFTER BasicAuthMiddleware -- Starlette wraps
    later-added middleware outermost, so this runs (and can still set a
    cookie) even on requests that end up 401'd.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path == _CAPTIVE_PATH:
            # Any visitor, their own device -- no login, no cookie to hang
            # state off of. Always trust the browser's own language.
            request.state.locale = match_accept_language(
                request.headers.get("accept-language", ""), SUPPORTED_LOCALES, DEFAULT_LOCALE
            )
            return await call_next(request)

        q = request.query_params.get("lang")
        cookie = request.cookies.get(LOCALE_COOKIE)
        if q in SUPPORTED_LOCALES:
            locale = q
        elif cookie in SUPPORTED_LOCALES:
            locale = cookie
        else:
            locale = DEFAULT_LOCALE
        request.state.locale = locale

        response = await call_next(request)
        if q in SUPPORTED_LOCALES and q != cookie:
            response.set_cookie(
                LOCALE_COOKIE, q, max_age=60 * 60 * 24 * 365, samesite="lax"
            )
        return response


def i18n_context_processor(request: Request) -> dict:
    """Registered on the single shared Jinja2Templates instance
    (app/templating.py) -- runs fresh per TemplateResponse() call with
    that call's own Request, so two concurrent requests in different
    languages never interfere (see app/templating.py for why this is
    concurrency-safe)."""
    locale = getattr(request.state, "locale", DEFAULT_LOCALE)
    t = get_translations(locale)
    return {
        "lang": locale,
        "_": t.gettext,
        "gettext": t.gettext,
        "ngettext": t.ngettext,
        "pgettext": t.pgettext,
    }
