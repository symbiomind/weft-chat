"""
Background pictures: one still image per weft, painted behind the donut.

A picture is furniture, like the weft it belongs to, so Weft may keep it. It keeps
it on disk rather than in the database, under ``data/backgrounds/``, and **never under
the static mount**: these are personal pictures and are served only through an API route
that checks the caller owns a weft wearing them. There is no URL that returns one to
someone who is not logged in.

**Every upload is re-encoded.** Nothing the browser sent is stored as sent:

* ``exif_transpose`` first, so a phone photo taken sideways stands up the way it does in
  the phone's own gallery.
* Re-encoding drops every byte of metadata. A phone photo carries the GPS position it was
  taken at, which is very often somebody's home, and Weft has no business keeping it.
* Shrunk to at most ``LONG_EDGE`` on its long side. Nothing larger is visible on any
  screen it is painted on, and the donut keeps a pre-scaled copy of it in memory.
* Saved as WebP, alpha kept, so a picture with a transparent background (a hologram, a
  cut-out) stays transparent over the theme.
* Anything Pillow cannot decode as one of ``FORMATS`` is :class:`NotAPicture` — the
  route turns it into a 422, never a 500. SVG never gets that far: it is not raster, and
  it is a script vector.

**Content-addressed.** The stored file is named after the SHA-256 of its re-encoded
bytes, and that hash is what the weft's ``background`` column holds. The same picture on
two wefts is one file; the URL built from the hash never changes meaning, so the browser
may cache it forever.

**Cleanup is a sweep.** A weft can lose its picture in several ways that know nothing
about files — deleted, its picture replaced, the user deleted — and each of those
cascades rows away in the database. Rather than teach every one of them about disk,
:func:`sweep` deletes any file no row references. It runs after every change and at
startup, and spares a file younger than ``SWEEP_GRACE_SECONDS`` so it can never delete a
picture whose row is still being committed.
"""

import hashlib
import io
import logging
import os
import re
import tempfile
import time
import warnings
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Weft

logger = logging.getLogger(__name__)

#: Refused before decoding. A full-resolution photo from a current phone is 5-15 MB.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

#: Refused before decoding, from the header alone. A 48-megapixel phone sensor is under
#: this; a decompression bomb (a tiny file declaring an enormous canvas) is not.
MAX_PIXELS = 64_000_000

#: The long edge of what is stored. Larger than any screen it lands on is wasted twice:
#: once on disk and once in the donut's cached layer.
LONG_EDGE = 2560

#: What the decoder will accept. Pillow can open far more than this; each extra format
#: is more decoder surface for no picture anybody uploads. GIF is here for the cat
#: memes, and only its first frame is kept — backgrounds are still.
FORMATS = ("JPEG", "PNG", "WEBP", "GIF")

WEBP_QUALITY = 85

#: A file younger than this is never swept: its row may be in a commit that has not
#: landed yet.
SWEEP_GRACE_SECONDS = 600

#: The only shape a stored name or a requested one may take.
HASH_PATTERN = r"^[0-9a-f]{64}$"
_HASH = re.compile(HASH_PATTERN)


class NotAPicture(ValueError):
    """The upload could not be turned into a background. The message is user-facing."""


def directory() -> Path:
    """Where the pictures live. Read per call so a test can point it somewhere else."""
    return Path(os.getenv("WEFT_BACKGROUNDS_DIR", "data/backgrounds"))


def path_for(digest: str) -> Path | None:
    """The file for a hash, or None if the hash is not one — never a path built from
    an arbitrary string."""
    if not _HASH.match(digest):
        return None
    return directory() / f"{digest}.webp"


def encode(data: bytes, *, long_edge: int = LONG_EDGE) -> bytes:
    """Turn an upload into the WebP that is stored. CPU-bound: call in a threadpool.

    Also the pipeline for pictures attached to a message (app/attachments.py), which
    only want a smaller ``long_edge``.
    """
    if not data:
        raise NotAPicture("That file is empty.")

    # Pillow warns at its own ceiling and raises at twice it. Both are errors here, and
    # MAX_PIXELS is checked from the header before anything is decoded.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(data), formats=FORMATS)
            if img.width * img.height > MAX_PIXELS:
                raise NotAPicture("That picture is too large. Try one under 64 megapixels.")
            img.load()
            img = ImageOps.exif_transpose(img)
        except NotAPicture:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise NotAPicture("That picture is too large. Try one under 64 megapixels.") from exc
        except Exception as exc:
            # UnidentifiedImageError, a truncated file, a decoder bug: all of them mean
            # the same thing to the person holding the file.
            raise NotAPicture("That file is not a picture Weft can use (JPEG, PNG, WebP or GIF).") from exc

    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    )
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    # A fresh image carries no info dict from the upload, and exif=b"" says so outright:
    # nothing from the original file survives into the stored one.
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=4, exif=b"")
    return out.getvalue()


def store(encoded: bytes) -> str:
    """Write an encoded picture under its own hash and return the hash."""
    return store_in(directory(), encoded)


def store_in(folder: Path, encoded: bytes) -> str:
    """Write an encoded picture into `folder` under its own hash and return the hash.

    Atomic — written beside its final name and renamed — so a reader never sees half a
    file. Storing a picture that is already there is a no-op, apart from refreshing its
    age so a sweep running in the same moment cannot take it.
    """
    digest = hashlib.sha256(encoded).hexdigest()
    target = folder / f"{digest}.webp"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        os.utime(target)
        return digest

    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return digest


async def sweep(session: AsyncSession) -> int:
    """Delete every stored picture no weft references. Returns how many went.

    Best effort: a failure is logged and swallowed, because an orphaned file costs some
    disk and a failed request over one costs the user their change.
    """
    root = directory()
    if not root.is_dir():
        return 0

    try:
        referenced = set(
            (
                await session.execute(
                    select(Weft.background)
                    .where(Weft.background.is_not(None))
                    .distinct()
                )
            ).scalars()
        )

        cutoff = time.time() - SWEEP_GRACE_SECONDS
        removed = 0
        for entry in root.iterdir():
            # Only our own names. Anything else in the directory was put there by
            # somebody else, and is theirs.
            stem, suffix = entry.stem, entry.suffix
            if suffix != ".webp" or not _HASH.match(stem) or stem in referenced:
                continue
            if entry.stat().st_mtime > cutoff:
                continue
            entry.unlink(missing_ok=True)
            removed += 1
        if removed:
            logger.info("Swept %d unused background picture(s).", removed)
        return removed
    except Exception:
        logger.exception("Sweeping background pictures failed; leaving them in place.")
        return 0
