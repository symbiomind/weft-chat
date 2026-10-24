"""
ELIZA: the built-in weft that needs no engine.

Joseph Weizenbaum wrote ELIZA at MIT in 1966: a few hundred lines of MAD-SLIP and a
script called DOCTOR that played a Rogerian psychotherapist. She was the first program
people mistook for a mind. ``engine.py`` and ``doctor.yml`` are ind-bridge's
``Weizenbaum`` plugin, ported unchanged in the engine and lightly in the script, so the
two projects keep one ELIZA rather than two drifting apart.

**No AI anywhere in her path.** The chat route answers her itself: no provider, no key,
no network. That is the point of her, and it means she works on a fresh install before
anyone has added an engine.

**She knows her way around Weft.** ``weft.yml`` is a second script in DOCTOR's schema,
laid over it, whose keywords say where things are in her own register. Anything it has
no entry for falls through to 1966.

**She is not a weft row.** Her id is fixed (``ELIZA_ID``) and nothing in the database
names her: there is nothing to configure, so no settings screen can show her. The
client keeps her thread like any other, and sends all of it every turn, which is all
her MEMORY mechanism and her depth-gated awakening need.

**She types.** The reply is streamed a word at a time at a rate that drifts between
five and ten words a second, as ind-bridge paces her.
"""

import asyncio
import logging
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import cache
from pathlib import Path

import yaml

from app.eliza import engine

logger = logging.getLogger(__name__)

#: The id the client sends for her, in place of a weft's.
ELIZA_ID = "eliza"

HERE = Path(__file__).parent

#: Said in character when the engine fails, instead of failing the turn.
LOST = "We may have wandered somewhere I cannot follow. Please go on."

#: Words a second: the pace drifts between these, never jumping more than a step.
MIN_WPS = 5.0
MAX_WPS = 10.0

#: How she waits between words. A name of its own so a test can run her at full speed.
_pause = asyncio.sleep


def merge(base: dict, over: dict) -> dict:
    """``over`` laid on ``base``: its keywords after base's, so a word both define
    answers from ``over`` (the engine lets the last of equal depth win), and its
    synonyms, pre and post merged in with its own values winning."""
    merged = dict(base)
    for table in ("pre", "post", "synonyms"):
        merged[table] = {**(base.get(table) or {}), **(over.get(table) or {})}
    merged["keywords"] = [*(base.get("keywords") or []), *(over.get("keywords") or [])]
    merged["awakening"] = [*(base.get("awakening") or []), *(over.get("awakening") or [])]
    if "greeting" in over:
        merged["greeting"] = over["greeting"]
    return merged


def _read(name: str) -> dict:
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8")) or {}


@cache
def script() -> engine.Script:
    """DOCTOR with Weft's knowledge laid over it. Read once: the files are code."""
    return engine.parse_script(merge(_read("doctor.yml"), _read("weft.yml")))


def reply(user_turns: list[str], name: str | None = None) -> str:
    """Her answer to the last of ``user_turns``, the user's side of the thread in order.

    Never raises: she has been up since 1966, and an error is answered in character.
    """
    name = (name or "").strip() or None
    try:
        return engine.respond([(text, name) for text in user_turns], script())
    except Exception:
        logger.exception("ELIZA could not answer a turn")
        return LOST


async def typed(
    text: str,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    rng: random.Random | None = None,
) -> AsyncIterator[str]:
    """``text`` a word at a time, each with the space after it, at a drifting pace."""
    sleep = sleep or _pause
    rng = rng or random.Random()
    wps = rng.uniform(MIN_WPS, MAX_WPS)
    for index, word in enumerate(re.findall(r"\S+\s*", text)):
        if index:
            wps = min(MAX_WPS, max(MIN_WPS, wps + rng.uniform(-1.0, 1.0)))
            await sleep(1.0 / wps)
        yield word
