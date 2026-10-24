"""
Session routes: the window's read of a thread kept in a session store.

A weft set to ``history: session`` keeps its conversation in a memory-mcp-ce store
(app/sessions.py). Writing happens in the chat relay, after each reply has streamed,
because that is the one place that has both halves of the exchange. Reading happens
here, when the window opens the weft or comes back into view — which is how a thread
continued on the phone shows up on the desktop.

The browser never holds the store's token and never names a session id; it names a
weft, and the service works out the rest from rows it owns. That is what stops one
device of one user reading any session the token can see.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, deps, sessions, upstream
from app.attachments import Attachment
from app.models import User, Weft
from app.routes.providers import _open_or_500, owned_provider
from app.routes.wefts import owned_weft

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/session",
    tags=["session"],
    dependencies=[
        Depends(deps.require_db_healthy),
        Depends(deps.require_vault_available),
        Depends(deps.require_csrf),
    ],
)

#: The store's own ceiling on a turn read.
MAX_TAIL_TURNS = 500

#: How many events a catch-up read asks for. A window that has fallen further behind
#: than this (another device held a long conversation) gets the whole tail instead,
#: which is what it would have got before catch-up reads existed.
CATCH_UP_EVENTS = 200


class Message(BaseModel):
    role: str
    content: str
    #: Pictures on this message, by reference. Empty for most.
    attachments: list[Attachment] = []
    #: Spoken through push to talk rather than typed.
    spoken: bool = False
    #: The exchange this message belongs to, in the store's numbering.
    turn: int | None = None


class ThreadOut(BaseModel):
    #: The session id in use, so the panel and the logs can say which session this is.
    session_key: str
    #: The newest turns, oldest first, as the window renders them.
    messages: list[Message]
    #: Whether the store holds older turns than these. Always false on a catch-up,
    #: which does not look at older turns.
    has_more: bool
    #: Where the session ends: the number /head reports, so a window can tell whether
    #: the store has moved on since it last read.
    last_seq: int
    #: True when ``messages`` is the thread's tail, to replace what the window holds.
    #: False when it is a catch-up: only what came after the ``after_seq`` the window
    #: asked with, to add to what it holds.
    full: bool = True


class HeadOut(BaseModel):
    session_key: str
    #: The session's last event number. 0 for a session not created yet.
    last_seq: int


async def resolve_store(
    session: AsyncSession, user: User, weft: Weft,
) -> tuple[sessions.Store, Weft] | None:
    """This user's weft and its store, opened — or None if the weft keeps no session.

    Shared with the chat relay, for the same reason ``owned_provider`` is shared: two
    copies of an ownership walk is one copy too many.
    """
    store = None
    if weft.session_provider_id:
        # Through owned_provider again: the weft names it, but the rule is the rule.
        store = await owned_provider(session, user, weft.session_provider_id)
    if not sessions.keeps_session(weft, store):
        return None

    return sessions.store_for(user, weft, store, _open_or_500(store, user)), weft


async def keep_exchange(store: sessions.Store, user_text: str, reply: str, *,
                        model: str, sent_at: str,
                        user_body: dict | None = None) -> str | None:
    """Append one finished exchange to the store. Returns a notice for the user on
    failure, None on success.

    Never raises: the reply has already been shown, and a store that is down must cost
    the user a saved turn, not the answer they just read.
    """
    try:
        if store.joined:
            # A typed id joins a session as it stands, whoever made it.
            if await upstream.session_handshake(store.base_url, store.secret, store.session_id) is None:
                await upstream.ensure_session(
                    store.base_url, store.secret, store.session_id,
                    agent=store.agent, user=store.user,
                )
        else:
            await upstream.ensure_session(
                store.base_url, store.secret, store.session_id,
                agent=store.agent, user=store.user,
            )
        await upstream.append_session_events(
            store.base_url, store.secret, store.session_id,
            [
                {"kind": "message", "role": "user", "text": user_text, "sent_at": sent_at,
                 **({"body": user_body} if user_body else {})},
                {"kind": "message", "role": "assistant", "text": reply, "model": model},
            ],
        )
        return None
    except upstream.UpstreamError as exc:
        return f"This exchange was not saved to the session store: {exc}"
    except Exception:
        logger.exception("Saving an exchange to session %s failed", store.session_id)
        return "This exchange was not saved to the session store (unexpected error)."


@router.get("/thread", response_model=ThreadOut)
async def read_thread(
    weft_id: str = Query(min_length=1, max_length=36),
    after_seq: int | None = Query(default=None, ge=0),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> ThreadOut:
    """The tail of one weft's session, as many turns as its window holds.

    With ``after_seq``, a catch-up instead: only the messages after that seq, for a
    window that already holds the thread up to it. This is the read the window makes
    when /head says the session has moved on, and it is cheap where the tail is not:
    the store decrypts every event it returns, so a long thread's tail costs tens of
    seconds and the two events a new exchange added cost milliseconds.

    A catch-up falls back to the tail (``full: true``) when it cannot be one: the
    session ends before ``after_seq`` (it was replaced or cleared), or more happened
    since than a catch-up reads.
    """
    resolved = await resolve_store(session, user, await owned_weft(session, user, weft_id))
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That weft does not keep its thread in a session store.",
        )
    store, weft = resolved

    if after_seq is not None:
        try:
            since = await upstream.session_since(
                store.base_url, store.secret, store.session_id,
                after_seq=after_seq, limit=CATCH_UP_EVENTS,
            )
        except upstream.UpstreamError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        if since is None:
            return ThreadOut(session_key=store.session_id, messages=[], has_more=False, last_seq=0)
        last_seq = int(since.get("last_seq") or 0)
        if last_seq >= after_seq and not since.get("has_more"):
            return ThreadOut(
                session_key=store.session_id,
                messages=[Message(**m) for m in sessions.messages_from_events(since.get("events", []))],
                has_more=False,
                last_seq=last_seq,
                full=False,
            )

    try:
        tail = await upstream.session_tail(
            store.base_url, store.secret, store.session_id,
            last_turns=min(weft.window_turns, MAX_TAIL_TURNS),
        )
    except upstream.UpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if tail is None:
        # Not created yet: the first finished exchange creates it.
        return ThreadOut(session_key=store.session_id, messages=[], has_more=False, last_seq=0)

    return ThreadOut(
        session_key=store.session_id,
        messages=[Message(**m) for m in sessions.messages_from_events(tail.get("events", []))],
        has_more=bool(tail.get("has_more")),
        last_seq=int(tail.get("last_seq") or 0),
    )


@router.get("/head", response_model=HeadOut)
async def read_head(
    weft_id: str = Query(min_length=1, max_length=36),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> HeadOut:
    """Where one weft's session ends, and nothing else.

    The cheap question a window asks every few seconds while it is open: "has another
    device added to this thread?" It reads one row's summary in the store — no event,
    nothing decrypted — so polling it costs next to nothing at either end. When the
    answer differs from what the window last read, the window asks /thread.
    """
    resolved = await resolve_store(session, user, await owned_weft(session, user, weft_id))
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That weft does not keep its thread in a session store.",
        )
    store, _ = resolved

    try:
        handshake = await upstream.session_handshake(store.base_url, store.secret, store.session_id)
    except upstream.UpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    last_seq = int(handshake.get("last_seq") or 0) if handshake else 0
    return HeadOut(session_key=store.session_id, last_seq=last_seq)
