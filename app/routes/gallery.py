"""
The gallery page: everything in it, and deleting from it.

Adding goes through ``PUT /api/attachments`` like any other upload, and lands with no
weft shown it; sending it in a weft's chat is what shows it to that weft. See
app/gallery.py.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments, db, deps, gallery
from app.models import User

router = APIRouter(
    prefix="/api/gallery",
    tags=["gallery"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


class GalleryItemOut(BaseModel):
    hash: str
    name: str
    #: ``picture`` or ``file``.
    kind: str
    #: What it is, from its bytes.
    type: str
    size: int
    #: ``user`` or ``weft``, or null for a picture from before the gallery.
    source: str | None
    #: The weft whose agent brought it, while that weft still exists.
    weft_id: str | None
    created_at: datetime
    #: The wefts that have been shown it, first shown first.
    shown_to: list[str]


class GalleryOut(BaseModel):
    items: list[GalleryItemOut]


@router.get("", response_model=GalleryOut)
async def list_gallery(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> GalleryOut:
    """Everything in the caller's gallery, newest first."""
    rows = await gallery.everything(session, user.id)
    return GalleryOut(items=[
        GalleryItemOut(**gallery.describe(item), source=item.source, weft_id=item.weft_id,
                       created_at=item.created_at, shown_to=shown)
        for item, shown in rows
    ])


@router.delete("/{digest}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    digest: str = Path(pattern=attachments.HASH_PATTERN),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> None:
    """Delete one of the caller's pictures or files, for good. Its references in chats
    stay, and show it as gone."""
    if not await gallery.remove(session, user.id, digest):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such file.")
