"""Groups CRUD -- name, hotspot_required, description, and the quota/
night-block fields (app/quota.py, app/mikrotik_quota.py). Not just the two
seeded rows: the admin can create as many groups as they want (e.g. a
"Crianças" group with a daily quota, or a "TV/Playstation" group that
needs no hotspot login at all but still curfews at a fixed hour).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Group
from app.schemas import GroupOut, GroupWithCount
from app.templating import templates

router = APIRouter()


def _groups_with_counts(db: Session) -> list[GroupWithCount]:
    counts = dict(
        db.execute(select(Device.group_id, func.count(Device.id)).group_by(Device.group_id)).all()
    )
    return [
        GroupWithCount(**GroupOut.model_validate(g).model_dump(), device_count=counts.get(g.id, 0))
        for g in db.scalars(select(Group))
    ]


def _get_group_or_404(db: Session, group_id: int) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(404, f"group {group_id} not found")
    return group


def _int_or_none(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def _str_or_none(v: str) -> str | None:
    v = (v or "").strip()
    return v or None


@router.get("/api/groups", response_model=list[GroupWithCount])
def api_list_groups(db: Session = Depends(get_db)):
    return _groups_with_counts(db)


@router.get("/groups", response_class=HTMLResponse)
def page_list_groups(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "groups_list.html", {"groups": _groups_with_counts(db)}
    )


@router.get("/groups/new", response_class=HTMLResponse)
def page_new_group(request: Request):
    return templates.TemplateResponse(request, "groups_form.html", {"group": None})


@router.get("/groups/{group_id}/edit", response_class=HTMLResponse)
def page_edit_group(request: Request, group_id: int, db: Session = Depends(get_db)):
    group = _get_group_or_404(db, group_id)
    return templates.TemplateResponse(request, "groups_form.html", {"group": group})


@router.post("/groups")
def post_create_group(
    name: str = Form(...),
    hotspot_required: str = Form(""),
    description: str = Form(""),
    daily_limit_weekday_s: str = Form(""),
    daily_limit_weekend_s: str = Form(""),
    night_block_start: str = Form(""),
    night_block_end: str = Form(""),
    night_block_address_list: str = Form(""),
    db: Session = Depends(get_db),
):
    group = Group(
        name=name.strip(),
        hotspot_required=hotspot_required == "on",
        description=_str_or_none(description),
        daily_limit_weekday_s=_int_or_none(daily_limit_weekday_s),
        daily_limit_weekend_s=_int_or_none(daily_limit_weekend_s),
        night_block_start=_str_or_none(night_block_start),
        night_block_end=_str_or_none(night_block_end),
        night_block_address_list=_str_or_none(night_block_address_list),
    )
    db.add(group)
    db.commit()
    return RedirectResponse(f"/groups/{group.id}/edit", status_code=303)


@router.post("/groups/{group_id}")
def post_update_group(
    group_id: int,
    name: str = Form(...),
    hotspot_required: str = Form(""),
    description: str = Form(""),
    daily_limit_weekday_s: str = Form(""),
    daily_limit_weekend_s: str = Form(""),
    night_block_start: str = Form(""),
    night_block_end: str = Form(""),
    night_block_address_list: str = Form(""),
    db: Session = Depends(get_db),
):
    group = _get_group_or_404(db, group_id)
    group.name = name.strip()
    group.hotspot_required = hotspot_required == "on"
    group.description = _str_or_none(description)
    group.daily_limit_weekday_s = _int_or_none(daily_limit_weekday_s)
    group.daily_limit_weekend_s = _int_or_none(daily_limit_weekend_s)
    group.night_block_start = _str_or_none(night_block_start)
    group.night_block_end = _str_or_none(night_block_end)
    group.night_block_address_list = _str_or_none(night_block_address_list)
    db.commit()
    return RedirectResponse(f"/groups/{group.id}/edit", status_code=303)
