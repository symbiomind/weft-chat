"""
Push icons: the picture a weft's notification wears on the device.

The middle square of the weft's background, with the donut over it in the weft's pair,
standing-wave mode, caught at the moment its swing is fullest. The same ring the chat
window draws (web/js/canvas/donut.js), its maths ported here so a closed window can
still be shown it. Without a background, the donut over the theme's floor; without a
pair, the theme's pair.

**Public, on purpose, and why that is acceptable.** A notification's icon is fetched by
the device with no login, so an icon is served to anyone who has its address. That is
the one exception to backgrounds.py's rule that a picture is only ever served to its
owner, and it is bounded:

* An icon is a 192-pixel square from the middle of the picture, not the picture, and it
  shows on the lock screen anyway.
* Its name is an HMAC keyed by the server's ``jwt.secret`` over the weft's picture and
  pair, not a hash of anything a stranger could enumerate. Without the secret there is
  nothing to count through; with it, the server is already lost.
* The service worker only shows an icon from this path, so a push cannot point the
  device at a picture somewhere else.

**Cached, made once.** The name changes when the picture or the pair does, so a change
makes a new icon on the next push and the sweep deletes the old one. Rendering is about
a third of a second of CPU, in a thread.
"""

import hashlib
import hmac
import logging
import math
import os
import re
import tempfile
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import backgrounds, config
from app.models import Weft

logger = logging.getLogger(__name__)

#: Bumped when the drawing changes, so every icon is drawn again.
VERSION = "v2"

#: The icon's size. What Android asks for, and plenty for a desktop notification.
SIZE = 192

#: Drawn this many times larger and shrunk, so the ring's edges come out smooth.
SUPERSAMPLE = 4

#: The theme's pair and floor (web/css/theme.css), for a weft that wears the theme.
THEME_A, THEME_B, FLOOR = "#7fb069", "#5d5d81", "#12141a"

#: The ring's radius as a share of the icon: the chat window's own, 0.42 of the long
#: side, so the tips of the swing are clipped by the edge just as they are on screen.
RADIUS = 0.42

#: The stroke, thicker than the chat window's, so it still reads at thumbnail size.
STROKE = 1.4

#: The standing wave (donut.js, WAVE_HARMONICS): lobes, flips per second, drift per
#: second, amplitude. At t = 0 every harmonic is at full swing.
HARMONICS = ((7, 0.47, 0.020, 0.27), (3, 0.31, 0.013, 0.18), (9, 0.63, 0.031, 0.035),
             (42, 0.09, 0.004, 0.004))
RING_POINTS = 384

NAME = re.compile(r"^[0-9a-f]{64}$")
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Same grace as the backgrounds: an icon written a moment ago is never swept.
SWEEP_GRACE_SECONDS = 600


def directory() -> Path:
    """Where icons are kept: beside the pictures, never under the static mount."""
    return Path(os.getenv("WEFT_PUSH_ICONS_DIR", "data/push-icons"))


def _pair(weft: Weft) -> tuple[str, str]:
    a, b = weft.colour_a or "", weft.colour_b or ""
    if _COLOUR.match(a) and _COLOUR.match(b):
        return a.lower(), b.lower()
    return THEME_A, THEME_B


def name_for(weft: Weft) -> str | None:
    """The icon's name: unguessable, and new whenever the picture or the pair changes.
    None when the server has no secret to key it with."""
    secret = config.jwt_secret()
    if not secret:
        return None
    a, b = _pair(weft)
    message = f"push-icon|{VERSION}|{weft.background or ''}|{a}|{b}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def path_for(name: str) -> Path | None:
    return directory() / f"{name}.png" if NAME.match(name or "") else None


def _rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _wave() -> list[float]:
    """donut.js's standingWave at t = 0, where every cosine is 1."""
    out = []
    for i in range(RING_POINTS):
        angle = i / RING_POINTS * math.tau
        out.append(0.5 + sum(amp * math.sin(lobes * angle) for lobes, _, _, amp in HARMONICS))
    return out


def render(background: Path | None, a: str, b: str) -> bytes:
    """Draw one icon as PNG bytes. CPU-bound: call in a thread."""
    side = SIZE * SUPERSAMPLE
    base = Image.new("RGBA", (side, side), FLOOR)
    if background is not None and background.is_file():
        with Image.open(background) as img:
            img = img.convert("RGBA")
            crop = min(img.size)
            left, top = (img.width - crop) // 2, (img.height - crop) // 2
            img = img.crop((left, top, left + crop, top + crop))
            base.alpha_composite(img.resize((side, side), Image.Resampling.LANCZOS))

    # As donut.js draws it: R, the ring's width, and the wave's reach of 3.2 rings.
    centre = side / 2
    radius = side * RADIUS
    ring = radius * 0.1
    radii = [radius + (w - 0.5) * ring * 3.2 for w in _wave()]

    def stroke(width: float) -> Image.Image:
        """The ring as a filled band, its edges `width / 2` either side of the wave.
        Filled rather than drawn as a thick line, whose joins leave teeth at this many
        points."""
        def outline(offset: float) -> list[tuple[float, float]]:
            return [
                (centre + math.cos(i / RING_POINTS * math.tau) * (r + offset),
                 centre + math.sin(i / RING_POINTS * math.tau) * (r + offset))
                for i, r in enumerate(radii)
            ]
        mask = Image.new("L", (side, side), 0)
        draw = ImageDraw.Draw(mask)
        draw.polygon(outline(width / 2), fill=255)
        draw.polygon(outline(-width / 2), fill=0)
        return mask

    # The canvas's radial gradient: A inside, B outside, across [R - ring, R + ring].
    gradient = Image.new("L", (side, side))
    pixels = gradient.load()
    low, span = radius - ring, 2 * ring
    for y in range(side):
        dy = y - centre
        for x in range(side):
            f = (math.hypot(x - centre, dy) - low) / span
            pixels[x, y] = 0 if f <= 0 else 255 if f >= 1 else round(f * 255)
    colour = Image.composite(
        Image.new("RGB", (side, side), _rgb(b)), Image.new("RGB", (side, side), _rgb(a)), gradient,
    )

    # Speaking mode's alpha, and its bloom: a blurred, slightly thinner pass on top.
    alpha = 0.8
    ring_alpha = stroke(ring * 1.3 * STROKE).point(lambda v: round(v * alpha))
    glow_alpha = stroke(ring * 1.1 * STROKE).filter(ImageFilter.GaussianBlur(ring * 0.9)).point(
        lambda v: round(v * alpha * 0.35 * 2))
    base.alpha_composite(Image.merge("RGBA", (*colour.split(), ring_alpha)))
    base.alpha_composite(Image.merge("RGBA", (*colour.split(), glow_alpha)))

    out = base.resize((SIZE, SIZE), Image.Resampling.LANCZOS).convert("RGB")
    buffer = BytesIO()
    out.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def ensure(weft: Weft) -> str | None:
    """The weft's icon name, drawing the icon first if it is not kept yet. CPU-bound:
    call in a thread. None when there is no secret to name it with."""
    name = name_for(weft)
    if name is None:
        return None
    target = path_for(name)
    if target.exists():
        os.utime(target)
        return name

    a, b = _pair(weft)
    background = backgrounds.path_for(weft.background) if weft.background else None
    data = render(background, a, b)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return name


async def sweep(session: AsyncSession) -> int:
    """Delete every icon no weft would be given now. Best effort, like the backgrounds'."""
    root = directory()
    if not root.is_dir():
        return 0
    try:
        wanted = {name_for(w) for w in (await session.scalars(select(Weft))).all()}
        cutoff = time.time() - SWEEP_GRACE_SECONDS
        removed = 0
        for entry in root.iterdir():
            if entry.suffix != ".png" or not NAME.match(entry.stem) or entry.stem in wanted:
                continue
            if entry.stat().st_mtime > cutoff:
                continue
            entry.unlink(missing_ok=True)
            removed += 1
        if removed:
            logger.info("Swept %d unused push icon(s).", removed)
        return removed
    except Exception:
        logger.exception("Sweeping push icons failed; leaving them in place.")
        return 0
