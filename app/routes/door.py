"""
The MCP door's keys, and its upload address.

Two routers, because they serve two different callers:

* ``router`` is for the user, in the browser: open a weft's door (mint its key), see
  whether it is open, close it, close them all. CSRF-checked like every other browser
  route.
* ``upload_router`` is for the agent, which has no Weft login: reading its one-time
  invite, and sending a file to the one-time address the ``upload`` tool handed out.
  No cookie and no CSRF: each address is the credential, works once, and lapses. See
  app/door.py.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments, backgrounds, db, deps, door
from app.models import User, Weft, WeftKey
from app.refresh import hash_token
from app.routes.providers import _read_capped
from app.routes.wefts import owned_weft

router = APIRouter(
    prefix="/api",
    tags=["door"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)

upload_router = APIRouter(
    prefix="/api/door",
    tags=["door"],
    dependencies=[Depends(deps.require_db_healthy)],
)


class DoorOut(BaseModel):
    #: True when the weft has a key, so an agent holding it can come in.
    open: bool
    #: The live key's last four characters, or null when the door is shut.
    hint: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    #: The address an agent's MCP config points at.
    mcp_url: str


class DoorKeyOut(DoorOut):
    #: The key itself. In this response only: it is never shown again.
    key: str
    #: The setup guide with the key in it, for a user connecting their agent by hand.
    guide: str


class InviteOut(BaseModel):
    #: The one-time address. Its first read mints the key and uses it up.
    url: str
    #: Seconds until it lapses unread.
    expires_in: int


class UploadOut(BaseModel):
    hash: str
    #: The line to write in a reply to show the picture.
    reference: str


async def _key_for(session: AsyncSession, weft: Weft) -> WeftKey | None:
    return (
        await session.execute(select(WeftKey).where(WeftKey.weft_id == weft.id))
    ).scalars().one_or_none()


def _door_out(key: WeftKey | None) -> DoorOut:
    if key is None:
        return DoorOut(open=False, mcp_url=door.mcp_url())
    return DoorOut(
        open=True,
        hint=key.hint,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        mcp_url=door.mcp_url(),
    )


@router.get("/wefts/{weft_id}/door", response_model=DoorOut)
async def get_door(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> DoorOut:
    """Whether this weft's door is open, and since when."""
    weft = await owned_weft(session, user, weft_id)
    return _door_out(await _key_for(session, weft))


@router.post(
    "/wefts/{weft_id}/door", response_model=DoorKeyOut, status_code=status.HTTP_201_CREATED
)
async def open_door(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> DoorKeyOut:
    """Mint this weft's key, replacing any it had. The old key stops working at once."""
    weft = await owned_weft(session, user, weft_id)
    raw, key = await _mint(session, weft)
    return DoorKeyOut(**_door_out(key).model_dump(), key=raw, guide=door.guide(raw, weft.name))


async def _mint(session: AsyncSession, weft: Weft) -> tuple[str, WeftKey]:
    """A new key for this weft, replacing any it had, with everything handed out under
    the old one closed."""
    raw = door.mint_key()
    key = await _key_for(session, weft)
    if key is None:
        key = WeftKey(weft_id=weft.id, token_hash="", hint="")
        session.add(key)
    key.token_hash = hash_token(raw)
    key.hint = raw[-4:]
    key.last_used_at = None
    await session.commit()
    await session.refresh(key)
    door.forget({weft.id})
    return raw, key


@router.post("/wefts/{weft_id}/door/invite", response_model=InviteOut)
async def invite(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> InviteOut:
    """A one-time address to hand the weft's agent in the chat.

    Reading it mints the key and serves the guide, so the key itself never passes
    through the chat. Any invite this weft already had waiting stops working.
    """
    weft = await owned_weft(session, user, weft_id)
    raw = door.open_invite(user.id, weft.id)
    return InviteOut(url=door.invite_url(raw), expires_in=door.INVITE_SECONDS)


@router.delete("/wefts/{weft_id}/door", status_code=status.HTTP_204_NO_CONTENT)
async def close_door(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> None:
    """Close this weft's door: its key stops working, and so do its upload addresses."""
    weft = await owned_weft(session, user, weft_id)
    await session.execute(delete(WeftKey).where(WeftKey.weft_id == weft.id))
    await session.commit()
    door.forget({weft.id})


@router.delete("/door/keys", status_code=status.HTTP_204_NO_CONTENT)
async def close_every_door(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> None:
    """Close every one of this user's doors at once."""
    weft_ids = set(
        (await session.execute(select(Weft.id).where(Weft.user_id == user.id))).scalars()
    )
    if weft_ids:
        await session.execute(delete(WeftKey).where(WeftKey.weft_id.in_(weft_ids)))
        await session.commit()
    door.forget(weft_ids)


@upload_router.get("/invite/{token}", response_class=PlainTextResponse)
async def read_invite(
    token: str = Path(pattern=r"^[A-Za-z0-9_-]{20,64}$"),
    session: AsyncSession = Depends(db.get_session),
) -> PlainTextResponse:
    """The page an invite points at: mints the weft's key and serves the guide, once.

    Read by the agent, which has no Weft login: the address is the credential. Markdown,
    because a model reads it. A second read gets a 410, which says the invite existed
    and is gone, rather than a 404 that could be a typo.
    """
    found = door.take_invite(token)
    weft = None
    if found is not None:
        weft = (
            await session.execute(
                select(Weft).where(Weft.id == found.weft_id, Weft.user_id == found.user_id)
            )
        ).scalars().one_or_none()
    if weft is None:
        return PlainTextResponse(
            "This Weft invite has been used or has lapsed. Ask your user for a new one.\n",
            status_code=status.HTTP_410_GONE,
        )
    raw, _ = await _mint(session, weft)
    return PlainTextResponse(door.guide(raw, weft.name), media_type="text/markdown")


@upload_router.put("/upload/{ticket}", response_model=UploadOut)
async def upload(
    request: Request,
    ticket: str = Path(pattern=r"^[A-Za-z0-9_-]{20,64}$"),
) -> UploadOut:
    """Store a picture sent to a one-time address from the ``upload`` tool.

    The address is used up by trying it, whether the picture turns out to be one or
    not: a second try needs a second address. The content type is not consulted.
    ``curl -T`` sends none, and every byte is decoded and re-encoded anyway, which is
    what refuses a file that is not a picture (app/backgrounds.py).
    """
    found = door.take_ticket(ticket)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That upload address is used, lapsed or unknown. Ask upload for another.",
        )

    data = await _read_capped(request, backgrounds.MAX_UPLOAD_BYTES)
    try:
        encoded = await run_in_threadpool(attachments.encode, data)
    except backgrounds.NotAPicture as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    digest = await run_in_threadpool(attachments.store, found.user_id, encoded)
    return UploadOut(hash=digest, reference=door.reference(digest, found.name))
