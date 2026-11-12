"""
The gallery: every picture and file put into a chat, by the user or by a weft's agent.

The gallery sits between the chat and wherever a file is really kept. A weft's agent
makes a song, finds a data sheet, draws a chart; it lands here and shows in the chat,
and later the user, or that agent, takes it somewhere permanent. Nothing here expires.
The user keeps or deletes what is in it.

**Two kinds of thing, one store.** A *picture* goes through the background pipeline
(app/attachments.py: re-encoded to WebP, every byte of metadata dropped) and is kept
as ``<hash>.webp``. Any other *file* is kept exactly as it was sent, as
``<hash>.bin``, if it is one of the kinds in ``sniff``. Both live in the user's own
folder, so the folder is still the ownership check: a request can only ever name a
hash, and the path is built from the caller's own id.

**What a row adds.** The folder says whose a file is. A row in ``gallery_items`` says
what it is (its name, its type, its size), and who brought it. A row in
``gallery_shares`` says which wefts have been shown it. A weft's agent can only find
what is shared with it: the files it brought, and the ones its user put in its chat.
Sharing a file with another weft means sending it there. An agent never comes across a
file nobody put in its window.

**Pictures from before the gallery** have a file and no row. One gets its row the
first time anything asks about it (``adopt``). Where it came from was never written
down, so its source is left empty, and it is shared with no weft until it is sent to
one again.
"""

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments, backgrounds
from app.models import GalleryItem, GalleryShare

#: The largest file the gallery takes. Cloudflare's tunnel refuses a request body over
#: 100 MB on its free plan, so anything bigger could not reach Weft through one anyway.
MAX_FILE_BYTES = 100 * 1024 * 1024

#: A picture over this is not tried as a picture: the pipeline holds it in memory.
MAX_PICTURE_BYTES = backgrounds.MAX_UPLOAD_BYTES

#: How much of a file ``sniff`` looks at.
_HEAD = 64 * 1024

#: What a name ending tells about a text file, for showing it and for the download.
_TEXT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
}

#: The ISO media brands that are sound only. Any other brand is taken as video.
_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "}


class NotAFile(ValueError):
    """The upload is neither a picture nor a kind of file the gallery keeps. The
    message is user-facing."""


def sniff(head: bytes, name: str = "") -> str | None:
    """The type of a file from its first bytes, or None if it is not one we keep.

    Only the bytes decide whether a file is kept. Its name only chooses between kinds
    of text. A sender's content type is never consulted: ``curl -T`` sends none, and
    a browser's guess comes from the name anyway.
    """
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0
                                   and head[1] & 0x06):
        return "audio/mpeg"
    if len(head) > 1 and head[0] == 0xFF and head[1] in (0xF1, 0xF9):
        return "audio/aac"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _AUDIO_BRANDS:
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        if brand[:3] in (b"hei", b"hev", b"mif", b"avi"):
            # HEIC and AVIF stills share the box format with video. A picture here is
            # one the picture pipeline could not read, kept with its metadata intact,
            # which is not what a picture should be.
            return None
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm" if name.lower().endswith(".weba") else "video/webm"
    if _is_text(head):
        # Markup is refused, not kept as text: a page or an SVG is somewhere script can
        # live, and nothing the gallery holds should ever be one a browser renders.
        if head.lstrip()[:20].lower().startswith(_MARKUP):
            return None
        return _TEXT_TYPES.get(Path(name).suffix.lower(), "text/plain")
    return None


_MARKUP = (b"<svg", b"<!doctype", b"<html", b"<?xml", b"<script")


#: Control characters text has no business holding. A program's header has them
#: within its first bytes; prose has none but these: tab, newline, form feed, return.
_BINARY = bytes(set(range(32)) - {9, 10, 12, 13}) + b"\x7f"


def _is_text(head: bytes) -> bool:
    if not head or len(head.translate(None, _BINARY)) != len(head):
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut off by the end of the head is still text.
        return exc.start >= len(head) - 3 and len(head) == _HEAD
    return True


def is_playable(mime: str) -> bool:
    return mime.startswith(("audio/", "video/"))


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


class TooLarge(ValueError):
    pass


async def receive(user_id: str, chunks: AsyncIterator[bytes], name: str, *,
                  picture_only: bool = False) -> dict:
    """Keep one upload: as a picture if it is one, as a file if it is a kind we keep.

    The body goes to a temporary file as it arrives, counted against the cap and
    hashed on the way, so a large file never sits in memory. Returns what the gallery
    row needs: ``hash``, ``kind`` (``picture`` or ``file``), ``mime`` and ``size``.
    Raises ``TooLarge``, ``backgrounds.NotAPicture`` or ``NotAFile``.

    ``picture_only`` is for a sender that said it was sending a picture: then it must
    be one, and is never kept as a file instead.
    """
    folder = attachments.user_folder(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=folder, suffix=".part")
    tmp = Path(tmp_name)
    try:
        digest = hashlib.sha256()
        size = 0
        head = b""
        with os.fdopen(fd, "wb") as fh:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise TooLarge(f"That file is over {MAX_FILE_BYTES // (1024 * 1024)} MB.")
                if len(head) < _HEAD:
                    head += chunk[: _HEAD - len(head)]
                digest.update(chunk)
                fh.write(chunk)
        if not size:
            raise NotAFile("That file is empty.")

        if size <= MAX_PICTURE_BYTES:
            try:
                encoded = await run_in_threadpool(attachments.encode, tmp.read_bytes())
            except backgrounds.NotAPicture as exc:
                picture_error = exc
            else:
                stored = await run_in_threadpool(attachments.store, user_id, encoded)
                return {"hash": stored, "kind": "picture", "mime": "image/webp",
                        "size": len(encoded)}
        else:
            picture_error = None
        if picture_only:
            raise picture_error or backgrounds.NotAPicture(
                f"That picture is over {MAX_PICTURE_BYTES // (1024 * 1024)} MB.")

        mime = sniff(head, name)
        if mime is None:
            if picture_error is not None and _looks_like_picture(head):
                raise picture_error
            raise NotAFile(
                "That is not a kind of file Weft keeps. It takes pictures, PDFs, sound, "
                "video and plain text."
            )
        hexdigest = digest.hexdigest()
        target = attachments.file_path(user_id, hexdigest)
        if target.exists():
            os.utime(target)
        else:
            os.replace(tmp, target)
        return {"hash": hexdigest, "kind": "file", "mime": mime, "size": size}
    finally:
        tmp.unlink(missing_ok=True)


def _looks_like_picture(head: bytes) -> bool:
    """Whether a file that failed the picture pipeline was meant to be a picture, so the
    picture's own reason is the one to give."""
    return (head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8"))
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


async def record(session: AsyncSession, user_id: str, received: dict, name: str, *,
                 source: str, weft_id: str | None = None) -> GalleryItem:
    """The row for something just received, made if it is new.

    The same bytes sent twice are one file, and keep the name and source they first
    came with. Commits.
    """
    item = await session.get(GalleryItem, (user_id, received["hash"]))
    if item is None:
        item = GalleryItem(
            user_id=user_id, hash=received["hash"],
            name=attachments.clean_name(name, received["kind"]),
            mime=received["mime"], size=received["size"], source=source, weft_id=weft_id,
        )
        session.add(item)
    if weft_id is not None:
        await _share(session, user_id, item.hash, weft_id)
    await session.commit()
    return item


async def adopt(session: AsyncSession, user_id: str, digest: str) -> GalleryItem | None:
    """The row for one of this user's hashes, made now for a picture from before the
    gallery. None if the user has no such file. Does not commit."""
    item = await session.get(GalleryItem, (user_id, digest))
    if item is not None:
        return item
    path = attachments.path_for(user_id, digest)
    if path is None or not path.is_file():
        return None
    # A file with no row is a picture: every other file was given its row as it came.
    item = GalleryItem(user_id=user_id, hash=digest, name="picture", mime="image/webp",
                       size=path.stat().st_size, source=None)
    session.add(item)
    await session.flush()
    return item


async def _share(session: AsyncSession, user_id: str, digest: str, weft_id: str) -> None:
    if await session.get(GalleryShare, (user_id, digest, weft_id)) is None:
        session.add(GalleryShare(user_id=user_id, hash=digest, weft_id=weft_id))
        await session.flush()


async def share(session: AsyncSession, user_id: str, digests: list[str], weft_id: str) -> None:
    """Record that these of the user's files were put in this weft's chat. A hash the
    user has no file for is passed over. Commits."""
    for digest in dict.fromkeys(digests):
        if await adopt(session, user_id, digest) is not None:
            await _share(session, user_id, digest, weft_id)
    await session.commit()


async def item_for(session: AsyncSession, user_id: str, digest: str) -> GalleryItem | None:
    """One of the user's files, described: its row, made now if it was missing."""
    item = await adopt(session, user_id, digest)
    if item is not None:
        await session.commit()
    return item


async def shared_with(session: AsyncSession, weft_id: str) -> list[GalleryItem]:
    """What a weft has been shown, newest first."""
    rows = await session.execute(
        select(GalleryItem)
        .join(GalleryShare, (GalleryShare.user_id == GalleryItem.user_id)
              & (GalleryShare.hash == GalleryItem.hash))
        .where(GalleryShare.weft_id == weft_id)
        .order_by(GalleryShare.shared_at.desc())
    )
    return list(rows.scalars())


def describe(item: GalleryItem) -> dict:
    """An item as the window and the agent see it."""
    return {
        "hash": item.hash,
        "name": item.name,
        "kind": attachments.kind_of(item.user_id, item.hash) or "file",
        "type": item.mime,
        "size": item.size,
    }
