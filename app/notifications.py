"""
Notifications: a weft's agent keeps its user posted.

The user sets a weft a long job and closes the window. The agent, wherever it runs,
reports in through its weft's MCP door with the ``notify`` tool (app/door.py), and the
report waits here until the user opens Weft and dismisses it. Nothing is pushed: the
window asks for counts on a timer, and the reports are simply there when it does.

**The sender is the door, and only the door.** A door key belongs to one weft and a weft
to one user, so a notification knows whose it is without being told. No door, no
notifications.

**Not part of the conversation.** A notification lives in Weft's own database whatever
the weft's history setting, and never touches its thread. It is a letter to the user,
not a turn.

**Dismissed is deleted.** There is no read state: waiting means not yet dismissed, and
dismissing removes the row. What a looping agent can pile up is bounded twice, by a rate
per hour and by a cap on what waits, past which the oldest goes.
"""

import time
from collections import deque

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification

#: Longest title, in characters. The column's width.
MAX_TITLE = 120

#: Longest body, in characters. A report, not a transcript.
MAX_BODY = 4000

#: Notifications one weft may send in an hour.
PER_HOUR = 30

#: Notifications one weft may have waiting. Past this, the oldest goes.
MAX_WAITING = 100


class TooMany(Exception):
    """A weft sent more notifications in the last hour than it may."""


#: When each weft sent its recent notifications, oldest first. In memory, like the
#: door's upload tickets: the service runs one worker, and a restart forgetting the
#: last hour costs nothing worse than one more hour's worth.
_sent: dict[str, deque[float]] = {}


def _take_slot(weft_id: str) -> None:
    now = time.monotonic()
    sent = _sent.setdefault(weft_id, deque())
    while sent and sent[0] <= now - 3600:
        sent.popleft()
    if len(sent) >= PER_HOUR:
        raise TooMany(
            f"This weft has sent {PER_HOUR} notifications in the last hour. Gather what "
            "is left into one report and send it later."
        )
    sent.append(now)


async def waiting(session: AsyncSession, weft_id: str) -> int:
    """How many of this weft's notifications are waiting to be dismissed."""
    return await session.scalar(
        select(func.count()).select_from(Notification).where(Notification.weft_id == weft_id)
    ) or 0


async def send(
    session: AsyncSession, *, user_id: str, weft_id: str, title: str, body: str,
) -> Notification:
    """Keep a report for the user. Raises TooMany past the hourly rate, and ValueError
    for a report with no title. Commits."""
    title = " ".join((title or "").split())
    body = (body or "").strip()
    if not title:
        raise ValueError("A notification needs a title.")
    if len(title) > MAX_TITLE:
        title = title[: MAX_TITLE - 1] + "…"
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - 1] + "…"

    _take_slot(weft_id)
    note = Notification(user_id=user_id, weft_id=weft_id, title=title, body=body)
    session.add(note)
    await session.flush()

    # The cap: keep the newest MAX_WAITING, so a stuck agent costs the oldest news,
    # never the newest.
    keep = (
        select(Notification.id)
        .where(Notification.weft_id == weft_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(MAX_WAITING)
    )
    await session.execute(
        delete(Notification).where(
            Notification.weft_id == weft_id, Notification.id.not_in(keep),
        )
    )
    await session.commit()
    return note


async def counts(session: AsyncSession, user_id: str) -> dict[str, int]:
    """Waiting notifications per weft, for the wefts that have any."""
    rows = await session.execute(
        select(Notification.weft_id, func.count())
        .where(Notification.user_id == user_id)
        .group_by(Notification.weft_id)
    )
    return {weft_id: n for weft_id, n in rows}


async def of_weft(session: AsyncSession, user_id: str, weft_id: str) -> list[Notification]:
    """A weft's waiting notifications, newest first. Empty for a weft not this user's."""
    rows = await session.scalars(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.weft_id == weft_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    return list(rows)


async def dismiss(session: AsyncSession, user_id: str, note_id: str) -> bool:
    """Dismiss one. False if there was no such notification of this user's. Commits."""
    result = await session.execute(
        delete(Notification).where(Notification.id == note_id, Notification.user_id == user_id)
    )
    await session.commit()
    return result.rowcount > 0


async def dismiss_all(session: AsyncSession, user_id: str, weft_id: str) -> int:
    """Dismiss every one a weft has waiting. Returns how many went. Commits."""
    result = await session.execute(
        delete(Notification).where(
            Notification.user_id == user_id, Notification.weft_id == weft_id,
        )
    )
    await session.commit()
    return result.rowcount
