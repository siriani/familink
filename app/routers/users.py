"""Family member records CRUD -- name/email/birthdate plus the personal
quota override (app/quota.py) and read-only visibility into today's
familink-tracked usage/blocked status. Linking a device to a user is still
done from the device detail page's existing "Owner" dropdown
(app/routers/devices.py) -- this page is only for managing the people
themselves.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import QuotaLog, User
from app.schemas import UserOut
from app.templating import templates

router = APIRouter()


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, f"user {user_id} not found")
    return user


def _int_or_none(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def _str_or_none(v: str) -> str | None:
    v = (v or "").strip()
    return v or None


def _date_or_none(v: str) -> date | None:
    v = (v or "").strip()
    return date.fromisoformat(v) if v else None


@router.get("/api/users", response_model=list[UserOut])
def api_list_users(db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.name)))


@router.get("/users", response_class=HTMLResponse)
def page_list_users(request: Request, db: Session = Depends(get_db)):
    users = list(db.scalars(select(User).order_by(User.name)))
    return templates.TemplateResponse(request, "users_list.html", {"users": users})


@router.get("/users/new", response_class=HTMLResponse)
def page_new_user(request: Request):
    return templates.TemplateResponse(request, "users_form.html", {"user": None})


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def page_edit_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    logs = list(
        db.scalars(
            select(QuotaLog)
            .where(QuotaLog.user_id == user.id)
            .order_by(desc(QuotaLog.applied_at))
            .limit(5)
        )
    )
    return templates.TemplateResponse(request, "users_form.html", {"user": user, "quota_logs": logs})


@router.post("/users")
def post_create_user(
    name: str = Form(...),
    email: str = Form(""),
    birthdate: str = Form(""),
    daily_limit_weekday_s: str = Form(""),
    daily_limit_weekend_s: str = Form(""),
    db: Session = Depends(get_db),
):
    user = User(
        name=name.strip(),
        email=_str_or_none(email),
        birthdate=_date_or_none(birthdate),
        daily_limit_weekday_s=_int_or_none(daily_limit_weekday_s),
        daily_limit_weekend_s=_int_or_none(daily_limit_weekend_s),
    )
    db.add(user)
    db.commit()
    return RedirectResponse(f"/users/{user.id}/edit", status_code=303)


@router.post("/users/{user_id}")
def post_update_user(
    user_id: int,
    name: str = Form(...),
    email: str = Form(""),
    birthdate: str = Form(""),
    daily_limit_weekday_s: str = Form(""),
    daily_limit_weekend_s: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _get_user_or_404(db, user_id)
    user.name = name.strip()
    user.email = _str_or_none(email)
    user.birthdate = _date_or_none(birthdate)
    user.daily_limit_weekday_s = _int_or_none(daily_limit_weekday_s)
    user.daily_limit_weekend_s = _int_or_none(daily_limit_weekend_s)
    db.commit()
    return RedirectResponse(f"/users/{user.id}/edit", status_code=303)


@router.post("/users/{user_id}/reset-today")
async def post_reset_today(user_id: int, db: Session = Depends(get_db)):
    """Manual early reset -- zero today's usage and unblock right now,
    same idea as the retired hotspot-admin panel's "Reset" button. The
    nightly job will reset everyone anyway; this is for "give them extra
    time today" without waiting for midnight.
    """
    from app.mikrotik_quota import unblock_device
    from app.sync import get_mikrotik_client

    user = _get_user_or_404(db, user_id)
    was_blocked = user.blocked
    user.seconds_used_today = 0
    user.blocked = False
    devices = list(user.devices)
    db.commit()

    if was_blocked:
        client = get_mikrotik_client()
        details = []
        all_ok = True
        for device in devices:
            result = await unblock_device(client, device)
            details.append(f"{device.mac}: {result.detail}")
            all_ok = all_ok and result.success
        db.add(
            QuotaLog(
                user_id=user_id,
                limit_s=None,
                success=all_ok,
                detail="unblocked (manual reset): " + "; ".join(details),
            )
        )
        db.commit()

    return RedirectResponse(f"/users/{user_id}/edit", status_code=303)
