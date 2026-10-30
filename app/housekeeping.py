"""
The sweep that deletes auth rows nothing needs any more.

Two tables only grow: ``refresh_tokens`` gains a row every time a device refreshes (about
every twelve minutes while the app is open), and ``login_attempts`` one per sign-in try.
Both already knew how to prune themselves; nothing ever asked them to.

**Why a task in the lifespan and not a TTL.** SQLite has no row expiry, and neither does
Postgres without an extension. A trigger could fake one, but it would hide the rule in
the database and need writing twice when the database changes. A loop in the one process
this service runs is the plain answer. Should weft ever run as several workers, every
worker would run its own sweep — harmless (the deletes are idempotent), only wasteful,
and the point to move this to a single scheduled job.
"""

import asyncio
import logging

from app import config, db, push_icons, ratelimit, refresh

logger = logging.getLogger(__name__)

#: Often enough that a busy day's rows do not pile up, rarely enough not to matter.
INTERVAL_SECONDS = 6 * 3600


async def sweep() -> None:
    """One pass. Never raises: a failed sweep is a log line, and the next one retries."""
    try:
        grace = int(config.get("security.session_purge_grace_days") or 7)
        async with db.get_sessionmaker()() as session:
            devices, tokens = await refresh.purge_expired(session, grace_days=grace)
            attempts = await ratelimit.prune(session)
            await session.commit()
            # Icons for a picture or pair since changed. Here because nothing else
            # notices: an icon is drawn by a push, never by the change itself.
            await push_icons.sweep(session)
    except Exception:
        logger.exception("Auth housekeeping failed; it will try again next interval.")
        return

    if devices or tokens or attempts:
        logger.info(
            "Auth housekeeping removed %d ended device(s), %d refresh token row(s), "
            "%d old login attempt(s).",
            devices, tokens, attempts,
        )


async def run() -> None:
    """Sweep now, then every ``INTERVAL_SECONDS`` until cancelled at shutdown."""
    while True:
        await sweep()
        await asyncio.sleep(INTERVAL_SECONDS)
