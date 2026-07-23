"""Read-only in the foundation phase: just the two seeded groups + device
counts. Create/edit/delete UI is future work (see SPEC.md).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Group
from app.schemas import GroupWithCount
from app.templating import templates

router = APIRouter()


def _groups_with_counts(db: Session) -> list[GroupWithCount]:
    counts = dict(
        db.execute(select(Device.group_id, func.count(Device.id)).group_by(Device.group_id)).all()
    )
    return [
        GroupWithCount(
            id=g.id,
            name=g.name,
            hotspot_required=g.hotspot_required,
            description=g.description,
            is_default=g.is_default,
            device_count=counts.get(g.id, 0),
        )
        for g in db.scalars(select(Group))
    ]


@router.get("/api/groups", response_model=list[GroupWithCount])
def api_list_groups(db: Session = Depends(get_db)):
    return _groups_with_counts(db)


@router.get("/groups", response_class=HTMLResponse)
def page_list_groups(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "groups_list.html", {"groups": _groups_with_counts(db)}
    )
