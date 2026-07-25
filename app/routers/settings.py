"""Admin-configurable runtime settings (app/models.py:Setting): the
Fingerbank API key (app/fingerbank.py) and the MQTT broker connection
(app/mqtt_publish.py). The two secret fields (fingerbank_api_key,
mqtt_password) follow the "blank submit = don't touch the existing
value" convention already used for the PIN field on users_form.html --
the form never echoes a real secret back into the page, and there's no
separate "clear" control. The non-secret MQTT fields (host/port/user/
topic prefix) are ordinary settings, always saved as submitted -- they
DO get echoed back and can be blanked out normally.
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
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "fingerbank_key_set": bool(get_setting(db, "fingerbank_api_key")),
            "mqtt_host": get_setting(db, "mqtt_host") or "",
            "mqtt_port": get_setting(db, "mqtt_port") or "1883",
            "mqtt_user": get_setting(db, "mqtt_user") or "",
            "mqtt_password_set": bool(get_setting(db, "mqtt_password")),
            "mqtt_topic_prefix": get_setting(db, "mqtt_topic_prefix") or "familink",
        },
    )


@router.post("/settings")
def post_settings(
    # str | None = Form(None), not str = Form("") -- the page has two
    # independent <form> sections (Fingerbank, MQTT) that each POST here
    # on their own. None means "this field wasn't in this particular
    # submission" (the other form was saved), as opposed to "" meaning
    # "submitted but left blank" -- conflating the two would blank out
    # the MQTT settings every time someone just saves the Fingerbank key.
    fingerbank_api_key: str | None = Form(None),
    mqtt_host: str | None = Form(None),
    mqtt_port: str | None = Form(None),
    mqtt_user: str | None = Form(None),
    mqtt_password: str | None = Form(None),
    mqtt_topic_prefix: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if fingerbank_api_key is not None:
        fingerbank_api_key = fingerbank_api_key.strip()
        if fingerbank_api_key:
            set_setting(db, "fingerbank_api_key", fingerbank_api_key)

    if mqtt_host is not None:
        set_setting(db, "mqtt_host", mqtt_host.strip())
    if mqtt_port is not None:
        mqtt_port = mqtt_port.strip()
        set_setting(db, "mqtt_port", mqtt_port if mqtt_port.isdigit() else "1883")
    if mqtt_user is not None:
        set_setting(db, "mqtt_user", mqtt_user.strip())
    if mqtt_topic_prefix is not None:
        set_setting(db, "mqtt_topic_prefix", mqtt_topic_prefix.strip() or "familink")
    if mqtt_password is not None:
        mqtt_password = mqtt_password.strip()
        if mqtt_password:
            set_setting(db, "mqtt_password", mqtt_password)

    return RedirectResponse("/settings", status_code=303)
