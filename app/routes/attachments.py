"""
Pictures and files for a message: upload one, describe one, fetch one back.

The upload happens when the file is chosen, before anything is sent, so the chat
request itself only ever carries hashes. See app/attachments.py for where the files
live and why the directory is the ownership check, and app/gallery.py for what is kept
and what each row says.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments, backgrounds, db, deps, gallery
from app.models import User

router = APIRouter(
    prefix="/api/attachments",
    tags=["attachments"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


class AttachmentOut(BaseModel):
    #: What the chat request names this by, and what fetches it back.
    hash: str
    #: ``picture`` or ``file``. A picture goes to the weft as a picture; a file as its
    #: name.
    kind: str
    name: str
    #: What it is, from its bytes: ``image/webp`` for every picture.
    type: str
    size: int


async def _body(request: Request):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > gallery.MAX_FILE_BYTES:
        raise gallery.TooLarge(f"That file is over {gallery.MAX_FILE_BYTES // (1024 * 1024)} MB.")
    async for chunk in request.stream():
        yield chunk


async def receive(request: Request, user_id: str, name: str, *,
                  picture_only: bool = False) -> dict:
    """An upload kept, or the HTTP error that says why it was not."""
    try:
        return await gallery.receive(user_id, _body(request), name,
                                     picture_only=picture_only)
    except gallery.TooLarge as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=str(exc)) from exc
    except (backgrounds.NotAPicture, gallery.NotAFile) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=str(exc)) from exc


@router.put("", response_model=AttachmentOut)
async def upload(
    request: Request,
    name: str = Query(default="", max_length=512),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> AttachmentOut:
    """Keep one picture or file for a message that is about to be sent.

    The body is the file itself: a raw body is what lets the size cap bite while it
    streams. A picture is re-encoded and what is stored is never what was sent (see
    app/backgrounds.py); any other file is kept as sent, if its bytes say it is a
    kind the gallery takes. The content type only says what was meant: SVG is refused
    outright (markup that can carry script), and anything sent as ``image/`` must turn
    out to be a picture.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "image/svg+xml":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Send the picture itself, as a JPEG, PNG, WebP or GIF.",
        )
    received = await receive(request, user.id, name,
                             picture_only=content_type.startswith("image/"))
    item = await gallery.record(session, user.id, received, name, source="user")
    return AttachmentOut(**gallery.describe(item))


@router.get("/{digest}/about", response_model=AttachmentOut)
async def about(
    digest: str = Path(pattern=attachments.HASH_PATTERN),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> AttachmentOut:
    """What one of the caller's own pictures or files is, without fetching it: what a
    bubble needs to draw the card for a file before anyone asks to play it."""
    item = await gallery.item_for(session, user.id, digest)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such file.")
    return AttachmentOut(**gallery.describe(item))


@router.get("/{digest}")
async def fetch(
    digest: str = Path(pattern=attachments.HASH_PATTERN),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> FileResponse:
    """One of the caller's own pictures or files.

    Looked up under the caller's own directory, so a hash someone else uploaded gets the
    same 404 as one nobody did. Cacheable forever, privately: the name is the hash of
    the bytes. A file that is not a picture is sent as a download under its own name,
    with the type its bytes were found to be, never one that a browser would render as
    a page.
    """
    path = attachments.path_for(user.id, digest)
    if path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such file.")
    headers = {"Cache-Control": "private, max-age=31536000, immutable"}
    if path.suffix == ".webp":
        return FileResponse(path, media_type="image/webp", headers=headers)

    item = await gallery.item_for(session, user.id, digest)
    mime = item.mime if item else "application/octet-stream"
    name = item.name if item else "file"
    headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return FileResponse(path, media_type=mime, headers=headers)
