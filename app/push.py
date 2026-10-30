"""
Push: ring a user's devices while Weft is closed.

The window already rings the device while it is open (web/js/notify.js). This is the
rest: a device that turned the switch on also subscribes here, and when an agent leaves
a notification through the door, each of that user's subscribed devices is sent a
push (app/webpush.py). Off unless the instance turns it on (``push.enabled``), because
the push goes through the browser vendor's service.

**Who, never what.** A push carries the weft's id and name and how many are waiting,
nothing of the notification itself. The service worker draws only that.

**A device, not just a user.** A subscription is tied to the signed-in device that made
it (``PushSubscription.session_id``), and a device signed out or run out is skipped, so a
shared browser never rings for whoever used it last.

**Best effort, and never in the agent's way.** The door's ``notify`` returns as soon
as the notification is kept; the push goes out after, in the background. A failure is
logged and dropped: the notification is still waiting on the bell.

**One push per weft per minute at most.** A second notification inside the minute does
not ring again at once; one push follows at the end of the minute, with the count as it
is then. The push service's Topic replaces an undelivered push of the same weft, so a
phone that was off wakes to one notification per weft.
"""

import asyncio
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import config, db, notifications, webpush
from app.models import AuthSession, PushSubscription, Weft, utcnow

logger = logging.getLogger(__name__)

#: The least time between two pushes for one weft.
MIN_GAP_SECONDS = 60

#: How long a push service keeps trying a device that is off. A day: past that the
#: news is on the bell anyway.
TTL_SECONDS = 24 * 3600

#: Longest weft name a push carries. The worker caps it too.
MAX_NAME = 60


class BadSubscription(ValueError):
    """A subscription this server will not send to."""


def key() -> bytes | None:
    """The VAPID key, when push is on and the key is good."""
    raw = config.push_key()
    if raw is None:
        return None
    try:
        webpush.private_key(raw)
    except ValueError:
        logger.error("push.key is not a valid P-256 private key — push is UNAVAILABLE.")
        return None
    return raw


def public_key() -> str | None:
    """What the browser subscribes with, or None when push is unavailable."""
    raw = key()
    return webpush.public_key(raw) if raw else None


def check(endpoint: str, p256dh: str, auth: str) -> None:
    """Refuse a subscription that is not a push service's, or whose keys are not keys.

    The host check is the important one: this server POSTs to the endpoint, so a
    subscription is otherwise a way to make it call any address it can reach.
    """
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise BadSubscription("A push endpoint must be an https address.")
    allowed = config.push_allowed_hosts()
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise BadSubscription("That push service is not one this server sends to.")
    try:
        receiver = webpush.unb64url(p256dh)
        secret = webpush.unb64url(auth)
    except ValueError as exc:
        raise BadSubscription("The subscription's keys are not base64url.") from exc
    if len(receiver) != 65 or receiver[0] != 4:
        raise BadSubscription("The subscription's p256dh is not a P-256 public key.")
    if len(secret) != 16:
        raise BadSubscription("The subscription's auth secret is not 16 bytes.")


async def subscribe(
    session: AsyncSession, *, user_id: str, session_id: str,
    endpoint: str, p256dh: str, auth: str,
) -> None:
    """Keep this device's subscription. Raises BadSubscription. Commits.

    Keyed on the endpoint: the same browser subscribing again, signed in as someone
    else or with new keys, moves the one row rather than adding a second.
    """
    check(endpoint, p256dh, auth)
    row = await session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is None:
        session.add(PushSubscription(
            user_id=user_id, session_id=session_id, endpoint=endpoint, p256dh=p256dh, auth=auth,
        ))
    else:
        row.user_id, row.session_id, row.p256dh, row.auth = user_id, session_id, p256dh, auth
    await session.commit()


async def unsubscribe(session: AsyncSession, *, user_id: str, endpoint: str) -> bool:
    """Forget this device's subscription. False if there was none of this user's. Commits."""
    result = await session.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == endpoint, PushSubscription.user_id == user_id,
        )
    )
    await session.commit()
    return result.rowcount > 0


async def _live(session: AsyncSession, user_id: str) -> list[PushSubscription]:
    """The user's subscriptions on devices still signed in."""
    rows = await session.scalars(
        select(PushSubscription)
        .join(AuthSession, AuthSession.id == PushSubscription.session_id)
        .where(
            PushSubscription.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
    )
    return list(rows)


def _topic(weft_id: str) -> str:
    """The push service's Topic: at most 32 base64url characters, so a digest."""
    return webpush.b64url(hashlib.sha256(weft_id.encode()).digest())[:32]


async def deliver(user_id: str, weft_id: str) -> None:
    """Send the weft's waiting count to every live device of the user's. Logs, never
    raises: there is nobody to raise to."""
    raw = key()
    if raw is None:
        return
    async with db.get_sessionmaker()() as session:
        weft = await session.get(Weft, weft_id)
        count = await notifications.waiting(session, weft_id)
        subscriptions = await _live(session, user_id)
    if weft is None or count == 0 or not subscriptions:
        return

    payload = json.dumps({
        "weft": weft_id, "name": weft.name[:MAX_NAME], "count": count,
    }).encode()
    delivered, gone = [], []
    async with httpx.AsyncClient(timeout=10) as client:
        for sub in subscriptions:
            try:
                status = await webpush.send(
                    client,
                    webpush.Subscription(sub.endpoint, sub.p256dh, sub.auth),
                    payload, raw_private=raw, subject=config.push_subject(),
                    ttl=TTL_SECONDS, topic=_topic(weft_id),
                )
            except (httpx.HTTPError, ValueError) as exc:
                # The endpoint is never logged: with the keys, it reaches the phone.
                logger.warning("A push to subscription %s failed: %s", sub.id, type(exc).__name__)
                continue
            if status in (404, 410):
                gone.append(sub.id)
            elif 200 <= status < 300:
                delivered.append(sub.id)
            else:
                logger.warning("A push to subscription %s was refused: %s", sub.id, status)

    async with db.get_sessionmaker()() as session:
        if gone:
            await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
        if delivered:
            await session.execute(
                update(PushSubscription)
                .where(PushSubscription.id.in_(delivered))
                .values(last_ok_at=utcnow())
            )
        await session.commit()


#: When each weft last rang, and the wefts with a push already waiting for the end of
#: their minute. In memory: one worker, and a restart costs at most one early ring.
_last: dict[str, float] = {}
_pending: set[str] = set()

#: Running sends, held so they are not collected before they finish.
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _later(user_id: str, weft_id: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        _last[weft_id] = time.monotonic()
        await deliver(user_id, weft_id)
    except Exception:
        logger.exception("A delayed push failed")
    finally:
        _pending.discard(weft_id)


async def _now(user_id: str, weft_id: str) -> None:
    try:
        await deliver(user_id, weft_id)
    except Exception:
        logger.exception("A push failed")


def announce(user_id: str, weft_id: str) -> None:
    """A notification was just kept for this weft: ring the user's devices, now or at
    the end of the weft's minute. Returns at once."""
    if key() is None:
        return
    gap = time.monotonic() - _last.get(weft_id, float("-inf"))
    if gap >= MIN_GAP_SECONDS:
        _last[weft_id] = time.monotonic()
        _spawn(_now(user_id, weft_id))
    elif weft_id not in _pending:
        _pending.add(weft_id)
        _spawn(_later(user_id, weft_id, MIN_GAP_SECONDS - gap))
