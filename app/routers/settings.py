"""Admin-configurable runtime settings (app/models.py:Setting) -- today
just the Fingerbank API key (app/fingerbank.py). A blank submit means
"don't touch the existing value" (the form never echoes the real value
back into the page, same convention as the PIN field on users_form.html)
-- there's no separate "clear" control, matching that same precedent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.settings import get_setting, set_setting
from app.templating import templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request, db: Session = Depends(get_db)):
    fingerbank_key_set = bool(get_setting(db, "fingerbank_api_key"))
    return templates.TemplateResponse(
        request, "settings.html", {"fingerbank_key_set": fingerbank_key_set}
    )


@router.post("/settings")
def post_settings(fingerbank_api_key: str = Form(""), db: Session = Depends(get_db)):
    fingerbank_api_key = fingerbank_api_key.strip()
    if fingerbank_api_key:
        set_setting(db, "fingerbank_api_key", fingerbank_api_key)
    return RedirectResponse("/settings", status_code=303)
