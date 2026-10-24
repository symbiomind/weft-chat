"""
Pictures for a message: upload one, fetch one back.

The upload happens when the picture is chosen, before anything is sent, so the chat
request itself only ever carries hashes. See app/attachments.py for where the files
live and why the directory is the ownership check.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import attachments, backgrounds, deps
from app.models import User
from app.routes.providers import _read_capped

router = APIRouter(
    prefix="/api/attachments",
    tags=["attachments"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)


class AttachmentOut(BaseModel):
    #: What the chat request names this picture by, and what fetches it back.
    hash: str


@router.put("", response_model=AttachmentOut)
async def upload(request: Request, user: User = Depends(deps.active_user)) -> AttachmentOut:
    """Store one picture for a message that is about to be sent.

    The body is the picture itself, as for a background: a raw body is what lets the
    size cap bite while it streams. SVG is refused (markup that can carry script), and
    what is stored is never what was sent; see app/backgrounds.py.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Send the picture itself, as a JPEG, PNG, WebP or GIF.",
        )

    data = await _read_capped(request, backgrounds.MAX_UPLOAD_BYTES)
    try:
        encoded = await run_in_threadpool(attachments.encode, data)
    except backgrounds.NotAPicture as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    digest = await run_in_threadpool(attachments.store, user.id, encoded)
    return AttachmentOut(hash=digest)


@router.get("/{digest}")
async def fetch(
    digest: str = Path(pattern=attachments.HASH_PATTERN),
    user: User = Depends(deps.active_user),
) -> FileResponse:
    """One of the caller's own pictures.

    Looked up under the caller's own directory, so a hash someone else uploaded gets the
    same 404 as one nobody did. Cacheable forever, privately: the name is the hash of
    the bytes.
    """
    path = attachments.path_for(user.id, digest)
    if path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such picture.")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
