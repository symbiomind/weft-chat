"""
Tips: the list the client walks a new user through, and how far this
account has got through it.

The list is every tip, hidden ones included: the client decides what shows. Progress is
kept with the account, so it follows the user to every device. Writes are small and
idempotent, one tip or a handful at a time, because the client finishes tips as the
user goes and two devices can do so at once. See ``app/tips.py`` for the files and the
rules.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, deps, tips
from app.models import User

router = APIRouter(
    prefix="/api/tips",
    tags=["tips"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


class TipOut(BaseModel):
    id: str
    #: The screen it shows on by itself.
    page: str
    #: core | advanced | secret.
    group: str
    #: Its place among the page's tips.
    order: int
    #: Tips that must be done, or not apply, before this one shows.
    after: list[str]
    #: The section of the page its (?) sits on, if any.
    section: str | None
    #: A CSS selector for what it points at, if anything.
    point_at: str | None
    #: The event that finishes it, as well as Got it.
    done_on: str | None
    #: The event that shows it, instead of a visit to its page.
    show_on: str | None
    #: When it applies at all; null for always.
    when: str | None
    #: Progress is kept against id + since, so changing since shows it again.
    since: str
    #: For a hidden tip: a line to show in its place before it is found, if any.
    hint: str | None = None
    title: str
    #: The body, rendered from the file's markdown.
    html: str


class MarkOut(BaseModel):
    """One tip this account has finished: a done tip, or a found hidden one."""

    id: str
    #: The `since` it was finished against. Done means this matches the tip's own.
    since: str
    at: datetime


class TipsOut(BaseModel):
    tips: list[TipOut]
    #: Tips show by themselves. Hidden tips show on their trigger either way.
    on: bool
    done: list[MarkOut]


class FinishIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=64)


class SwitchIn(BaseModel):
    on: bool


class RestartIn(BaseModel):
    #: `help`: every tip but the hidden ones, and tips back on. `found`: only those.
    what: Literal["help", "found"] = "help"


async def _state(session: AsyncSession, user: User) -> TipsOut:
    return TipsOut(
        tips=[TipOut(**tip) for tip in tips.load()],
        on=user.tips_on,
        done=[
            MarkOut(id=m.tip_id, since=m.since, at=m.done_at)
            for m in await tips.marks(session, user.id)
        ],
    )


@router.get("", response_model=TipsOut)
async def list_tips(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> TipsOut:
    """Every tip, in file order, and this account's progress through them."""
    return await _state(session, user)


@router.post("/done")
async def finish_tips(
    body: FinishIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> dict:
    """Mark tips finished. Unknown ids are ignored, and so is one already done."""
    added = await tips.finish(session, user.id, body.ids)
    try:
        await session.commit()
    except IntegrityError:
        # Another device marked the same tip a moment ago: done is done.
        await session.rollback()
    return {"added": added}


@router.put("/on")
async def switch_tips(
    body: SwitchIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> dict:
    """Tips on or off, for this account on every device."""
    user.tips_on = body.on
    await session.commit()
    return {"on": body.on}


@router.post("/restart", response_model=TipsOut)
async def restart_tips(
    body: RestartIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> TipsOut:
    """Start again. Restarting the help turns tips back on; forgetting finds does not."""
    await tips.forget(session, user.id, body.what)
    if body.what == "help":
        user.tips_on = True
    await session.commit()
    return await _state(session, user)
