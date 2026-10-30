"""
Push: a device subscribes to be rung while Weft is closed, and unsubscribes. See
``app/push.py``.

The window asks first whether this server pushes at all; when it does not, the switch
still rings the device while a tab is open, and says that is all it can do.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, deps, push, push_icons
from app.models import User

router = APIRouter(
    prefix="/api/push",
    tags=["push"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


#: The icons, apart: the device fetches one with no login and no CSRF token to show.
#: See app/push_icons.py for why that is acceptable.
icon_router = APIRouter(prefix="/api/push/icons", tags=["push"])


class PushOut(BaseModel):
    #: Whether this server sends pushes. False: the device is rung only while open.
    available: bool
    #: VAPID public key, base64url, for ``pushManager.subscribe``. Null when unavailable.
    public_key: str | None = None


class KeysIn(BaseModel):
    p256dh: str = Field(max_length=128)
    auth: str = Field(max_length=64)


class SubscriptionIn(BaseModel):
    """As the browser's ``PushSubscription.toJSON()`` gives it, less what is not used."""

    endpoint: str = Field(max_length=1024)
    keys: KeysIn


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=1024)


@router.get("", response_model=PushOut)
async def availability(user: User = Depends(deps.active_user)) -> PushOut:
    """Whether this server pushes, and the key to subscribe with if it does."""
    public = push.public_key()
    return PushOut(available=public is not None, public_key=public)


@router.put("/subscription", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(
    body: SubscriptionIn,
    request: Request,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> None:
    """Keep this device's subscription. Sent again on every visit, so a browser that
    renewed its subscription, or a device signed in again, stays current."""
    if push.key() is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This server does not send pushes.")
    try:
        await push.subscribe(
            session, user_id=user.id, session_id=request.state.session_id,
            endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth,
        )
    except push.BadSubscription as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=str(exc)) from exc


@router.delete("/subscription", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    body: EndpointIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> None:
    """Forget this device's subscription. Nothing to forget is not an error: the
    switch going off must always work."""
    await push.unsubscribe(session, user_id=user.id, endpoint=body.endpoint)


@icon_router.get("/{name}.png", response_class=FileResponse)
async def icon(name: str) -> FileResponse:
    """A weft's notification icon. Public: its name is the only key to it."""
    path = push_icons.path_for(name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such icon.")
    # Immutable: a changed picture or pair is a new name, never new bytes under this one.
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
