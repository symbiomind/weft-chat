"""
Notifications: what the wefts' agents have reported, for the user to
read and dismiss. They arrive through the MCP door's ``notify`` tool; see
``app/notifications.py``.

The counts are what the window polls, so they are the cheap one: one grouped count,
nothing else. The list is read only when the user opens the notifications page.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, deps, notifications
from app.models import User
from app.routes.wefts import owned_weft

router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


class CountsOut(BaseModel):
    #: Waiting notifications by weft id, for the wefts that have any.
    wefts: dict[str, int]


class NotificationOut(BaseModel):
    id: str
    weft_id: str
    title: str
    #: Plain text. A picture reference in it shows as the picture.
    body: str
    created_at: datetime


class DismissAllIn(BaseModel):
    #: Only this weft's. Left out, every weft's.
    weft_id: str | None = None


@router.get("/counts", response_model=CountsOut)
async def counts(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> CountsOut:
    """How many notifications each weft has waiting. Wefts with none are left out."""
    return CountsOut(wefts=await notifications.counts(session, user.id))


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    weft: str | None = Query(None, description="Only this weft's. Left out, every weft's."),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> list[NotificationOut]:
    """The waiting notifications, newest first: every weft's, or one weft's."""
    weft_id = (await owned_weft(session, user, weft)).id if weft is not None else None
    return [
        NotificationOut(
            id=n.id, weft_id=n.weft_id, title=n.title, body=n.body, created_at=n.created_at,
        )
        for n in await notifications.of_user(session, user.id, weft_id)
    ]


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(
    note_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> None:
    """Dismiss one notification. It is gone, on every device."""
    if not await notifications.dismiss(session, user.id, note_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such notification.")


@router.post("/dismiss")
async def dismiss_all(
    body: DismissAllIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> dict:
    """Dismiss every notification waiting: every weft's, or one weft's."""
    weft_id = (await owned_weft(session, user, body.weft_id)).id if body.weft_id else None
    return {"dismissed": await notifications.dismiss_all(session, user.id, weft_id)}
