"""
The chat relay: the route that makes a Pick a connection.

One endpoint. The browser posts a conversation and the id of a weft from its Pick
menu; this finds the provider and model the weft is bound to, opens that provider's
sealed bearer, posts to its ``/chat/completions``, and
streams the answer back. **The provider key never reaches the browser** — that is the
whole reason this route exists rather than the client talking to the gateway directly.

**Why the wire format is ours and not the upstream's.** It would be fewer lines to pass
OpenAI's SSE straight through. Two reasons not to:

1. *Shape variance.* Upstream deltas arrive as ``choices[0].delta.content``, or
   ``choices[0].message.content`` from a server that ignored ``stream: true``, or a bare
   ``text``. Normalising once here means the client has one shape to render, forever,
   across every gateway anyone points Weft at. The dumb client stays dumb.
2. *The socket.* A planned `[socket]` capability eventually moves this traffic to a
   WebSocket, and inbound messages arrive on it unbidden. A frame contract that is ours
   already survives that move; OpenAI's SSE does not, because it has no way to say
   "Juno said hello while you were reading".

The contract, deliberately tiny::

    data: {"delta": "some text"}
    data: {"error": "what went wrong, in the provider's own words"}
    data: [DONE]

and, between them, ``: keep-alive`` when nothing else has been sent for a while: an SSE
comment, which every client skips (see *The quiet* below).

**Errors before the first token are a 502; errors after it are an error frame.** That is
not a design choice, it is HTTP: once a 200 and its headers are on the wire there is no
status code left to change. So everything that can be checked is checked before the
response begins, and anything that fails later is reported inside the stream — where a
client can still render it next to the half-finished reply it belongs to.

**No conversation is stored.** Not here, not in a table, not anywhere. The message list
arrives from the client on every turn, is forwarded, and is forgotten. That is
the no-storage rule in code: a window shows what is already there, and the state lives upstream
in the gateway. A plain provider needs the history sent because the OpenAI protocol is
stateless; a bridge holds its own and may do as it likes with ours.

**A weft can keep its thread in a session store** (``history: session``, 2026-10-04).
Then, once the reply has streamed, this route appends the exchange — the user's message
and the reply, partial if the stream broke — to that store. Still not storing: the store
is upstream, like the gateway, and this route forwards to it exactly as it forwards to
the model. A store that fails costs a saved turn, reported in an error frame, never the
reply. Everything about the store is resolved before the response begins, so the stream
itself touches no database.

**A session kept upstream** (2026-11-24). Some engines keep the conversation themselves:
a bridge with a session store of its own writes each turn as the agent saw it, and
offers the person a read-only view of it. Weft finds out from the store, with no setting
to get wrong: before it asks the engine, it opens the session, and a store that answers
``read_only: true`` is that view. Then weft

- sends ``X-Session-Id`` with the session's id, so the engine writes the conversation
  this weft reads (each weft, and each typed id, its own);
- sends only what's new (system messages, and everything after the last reply),
  because the engine replays the rest itself, as the agent saw it;
- appends nothing: the engine did. The window catches up from the store as it does
  for another device's turn.

Only when the engine and the store share an origin, though. A derived id carries the
username, which a stranger's model API has no business seeing, and a read-only store
next to an unrelated engine is a weft set up wrong: the engine forgets, and the store
never fills. That case is said in an error frame on every turn, plainly, until fixed.

**The quiet** (2026-11-10). A proxy in front of Weft, Cloudflare's tunnel for one, closes
a response that sends nothing for about a hundred seconds. An agent working through tool
calls behind a gateway that streams only its words can be silent far longer than that,
and some gateways hold even their headers back until they have words to send. So the
stream says something when it has nothing to say: a ``: keep-alive`` comment after
``KEEPALIVE_SECONDS`` of silence. And the route waits only ``HEADER_GRACE_SECONDS`` for
the upstream's headers before starting its own reply anyway. An upstream that answers
inside that grace (nearly all of them, and every one that fails fast) still gets the
honest 502 above; one that is still thinking after it has its failures reported in the
stream, like any other failure after the 200.
"""

import asyncio
import json
import logging
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments, db, deps, eliza, gallery, upstream, vault
from app.attachments import MAX_PER_MESSAGE, Attachment
from app.models import User, utcnow
from app.routes.providers import owned_provider
from app.routes.sessions import keep_exchange, open_session, resolve_store
from app.routes.wefts import owned_weft

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/chat",
    tags=["chat"],
    dependencies=[
        Depends(deps.require_db_healthy),
        Depends(deps.require_vault_available),
        Depends(deps.require_csrf),
    ],
)

#: Per-message and per-conversation ceilings. Generous — a long thread is a normal
#: thing — but present, because without them one request can decide how much memory
#: this process allocates.
MAX_MESSAGE_CHARS = 32_768
MAX_MESSAGES = 200

#: How long the route waits for the upstream's headers before it begins its own reply.
#: Long enough that a bad token, a wrong URL or a gateway that is down still answers as
#: a status code; short enough that nothing in front of Weft has started counting.
HEADER_GRACE_SECONDS = 5.0

#: Silence on the stream after which a keep-alive goes out. Well inside the hundred
#: seconds Cloudflare allows, and the comment is a dozen bytes.
KEEPALIVE_SECONDS = 15.0

#: An SSE comment: a line starting with a colon. Clients skip it (web/js/session.js
#: parseFrame keeps only ``data:`` lines), so it keeps a proxy awake and nothing else.
KEEPALIVE = ": keep-alive\n\n"


class Message(BaseModel):
    #: Only the three roles the OpenAI protocol defines for input. A free string here
    #: would be forwarded verbatim to somebody else's parser.
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)
    #: Pictures and files on this message, by the hash PUT /api/attachments returned.
    #: Only the newest user message's pictures go upstream as pictures, and a file only
    #: ever as its name; see wire_messages.
    attachments: list[Attachment] = Field(default_factory=list, max_length=MAX_PER_MESSAGE)
    #: Spoken through push to talk rather than typed. The weft is told
    #: with a marker when the user wants it to be; see wire_messages.
    spoken: bool = False


async def wire_messages(messages: list[Message], user_id: str, *,
                        tell_spoken: bool = True) -> list[dict]:
    """The conversation as the provider receives it.

    Every message's text gets its pictures' and files' marker in front
    (``[image: photo.jpg] [file: song.mp3]``),
    so a reader that only keeps text, like ind-bridge's session storage, still knows a
    picture was there. A message with pictures and no words would otherwise be no text
    at all, and the bridge does not store a turn with no text.

    Only the NEWEST message, when it is the user's, carries its pictures as pictures:
    a list of content parts, the images first and the words after, each image a
    ``data:`` URI read from this user's own attachments. Earlier turns are their marker
    alone. A being looks at a picture once and remembers what it saw, and every later
    turn would otherwise pay the picture's tokens again. A file never goes as itself,
    only as its marker: a weft's agent that wants one takes it from the gallery.

    A spoken message gets ``[spoken]`` after any picture markers, when ``tell_spoken``
    (the user's own setting) says the weft should know.
    """
    wire = []
    last = len(messages) - 1
    for index, message in enumerate(messages):
        attached = attachments.describe(user_id, message.attachments)
        text = attachments.with_marker(
            message.content, attached, message.spoken and tell_spoken)
        pictures = [a for a in attached if a.get("kind") != "file"]
        if index != last or message.role != "user" or not pictures:
            wire.append({"role": message.role, "content": text})
            continue

        parts = []
        for attachment in pictures:
            uri = await run_in_threadpool(attachments.data_uri, user_id, attachment["hash"])
            if uri is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="A picture on this message is not on the server any more. "
                           "Remove it and attach it again.",
                )
            parts.append({"type": "image_url", "image_url": {"url": uri}})
        parts.append({"type": "text", "text": text})
        wire.append({"role": message.role, "content": parts})
    return wire


class ChatIn(BaseModel):
    weft_id: str = Field(min_length=1, max_length=36)
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)


def same_origin(a: str, b: str) -> bool:
    """Whether two base URLs are the same server: scheme, host and port."""
    def origin(url: str):
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        return scheme, (parts.hostname or "").lower(), parts.port or {"http": 80, "https": 443}.get(scheme)
    try:
        return origin(a) == origin(b)
    except ValueError:
        return False


def only_new(wire: list[dict]) -> list[dict]:
    """What an engine that keeps the conversation needs: the system messages, and
    everything after the last reply."""
    last_reply = max((i for i, m in enumerate(wire) if m["role"] == "assistant"), default=-1)
    return [m for i, m in enumerate(wire) if m["role"] == "system" or i > last_reply]


#: Said on every turn of a weft whose store is a read-only view of a session an
#: engine keeps, while it chats with a different engine.
ELSEWHERE = ("This weft's history is kept by the engine at {store}, but it chats with {engine}, "
             "so nothing is remembered. Point the weft's engine at {store} as well, or pick "
             "another history store for it.")


def _frame(obj: dict) -> str:
    """One SSE frame.

    ``json.dumps`` escapes newlines inside strings, which is what keeps a model that
    replies with a blank line from silently terminating its own frame — the failure
    that turns a long answer into a truncated one for no visible reason.
    """
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(
    body: ChatIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> StreamingResponse:
    """Relay one turn of conversation to whatever the weft is bound to."""
    # She is not a weft row and has no engine to relay to: she answers here.
    if body.weft_id == eliza.ELIZA_ID:
        return _eliza(body, user)

    weft = await owned_weft(session, user, body.weft_id)

    # A weft with no engine is on the Pick menu but has nobody to answer it: added a
    # moment ago, or its provider was deleted. 409, with the way out in the message.
    if not weft.bound():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{weft.name} has no engine yet. Give it one on its page (⚙ on the "
                   f"Pick menu).",
        )
    provider = await owned_provider(session, user, weft.provider_id)
    model_id = weft.model_id

    try:
        secret = vault.open_secret(
            provider.secret_ciphertext,
            provider.secret_nonce,
            provider.key_id,
            user_id=user.id,
            provider_id=provider.id,
        )
    except vault.VaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This provider's stored token could not be opened on the server.",
        ) from exc

    # The session store, if this weft keeps one. Resolved (and its token opened) here,
    # before the response begins: a misconfigured store is then a clean error status
    # rather than something discovered with the stream half-written.
    resolved = await resolve_store(session, user, weft)
    store = resolved[0] if resolved else None
    last = body.messages[-1]
    attached = attachments.describe(user.id, last.attachments) if last.role == "user" else []
    spoken = last.role == "user" and last.spoken
    user_text = (
        attachments.with_marker(last.content, attached, spoken and user.stt_tell_weft)
        if last.role == "user" else None
    )
    # Put in this weft's chat, so from now on its agent may find them in the gallery.
    if attached:
        await gallery.share(session, user.id, [a["hash"] for a in attached], weft.id)
    # The store keeps the pictures and files as references, never their bytes, and
    # remembers a spoken message as spoken even when the weft was not told.
    user_body = {
        **({"attachments": attached} if attached else {}),
        **({"spoken": True} if spoken else {}),
    } or None
    sent_at = utcnow().isoformat()

    wire = await wire_messages(body.messages, user.id, tell_spoken=user.stt_tell_weft)

    # Who keeps the conversation: weft (appending each exchange), or the engine, which
    # the store says by answering read-only (see *A session kept upstream*). A store
    # that can't be reached now leaves weft keeping it, and the append reports it.
    opened, kept_upstream, store_notice = False, False, None
    if store is not None:
        try:
            head = await open_session(store)
            opened = True
        except upstream.UpstreamError:
            head = {}
        except Exception:
            logger.exception("Opening session %s failed", store.session_id)
            head = {}
        if isinstance(head, dict) and head.get("read_only") is True:
            if same_origin(provider.base_url, store.base_url):
                kept_upstream = True
            else:
                store_notice = ELSEWHERE.format(store=store.base_url, engine=provider.base_url)

    payload = {
        "model": model_id,
        "messages": only_new(wire) if kept_upstream else wire,
        "stream": True,
    }

    opening = asyncio.ensure_future(upstream.stream_chat(
        provider.base_url, secret, payload, extra_headers=weft.headers,
        **({"session_id": store.session_id} if kept_upstream else {}),
    ))
    try:
        deltas = await asyncio.wait_for(asyncio.shield(opening), HEADER_GRACE_SECONDS)
    except TimeoutError:
        # Still thinking. Begin the reply and go on waiting inside it, where the
        # keep-alives can reach the proxy (see "The quiet" above).
        deltas = None
    except upstream.UpstreamError as exc:
        # Nothing has been sent yet, so this can still be an honest status code.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except BaseException:
        opening.cancel()
        raise

    async def body_stream():
        reply: list[str] = []
        try:
            try:
                stream = deltas if deltas is not None else await opening
                async for delta in stream:
                    reply.append(delta)
                    yield _frame({"delta": delta})
            except upstream.UpstreamError as exc:
                yield _frame({"error": str(exc)})
            except Exception:
                # The upstream generator owns a live socket; anything unexpected escaping
                # it would otherwise reach the client as a truncated reply with no
                # explanation at all. Log the trace, tell the user something broke.
                logger.exception("Chat relay failed mid-stream for provider %s", provider.id)
                yield _frame({"error": "The connection to that provider broke mid-reply."})

            # Only a reply that arrived is kept — the same rule the window applies to its
            # own copy, so the store and the window agree about what was said.
            # The engine kept it, or the store is a view that keeps nothing from us.
            if store_notice:
                yield _frame({"error": store_notice})
            elif store is not None and user_text and reply and not kept_upstream:
                notice = await keep_exchange(
                    store, user_text, "".join(reply), model=model_id, sent_at=sent_at,
                    user_body=user_body, opened=opened,
                )
                if notice:
                    yield _frame({"error": notice})
        finally:
            # A reply abandoned before the upstream answered leaves the opening running.
            opening.cancel()
            yield "data: [DONE]\n\n"

    return _stream(body_stream())


def _eliza(body: ChatIn, user: User) -> StreamingResponse:
    """ELIZA's turn: the reply is computed here and typed out (see app/eliza).

    Only the words of the user's turns reach her. Pictures and the spoken marker are
    for engines that can use them; she was written for a teletype.
    """
    answer = eliza.reply(
        [m.content for m in body.messages if m.role == "user"], user.display_name)

    async def body_stream():
        try:
            async for word in eliza.typed(answer):
                yield _frame({"delta": word})
        finally:
            yield "data: [DONE]\n\n"

    return _stream(body_stream())


async def _keepalive(frames, every: float | None = None):
    """``frames``, with a ``KEEPALIVE`` wherever ``every`` seconds pass without one.

    The next frame is awaited as a task rather than directly, so the wait can be
    interrupted by the clock without cancelling it: the same pending frame is waited on
    again after each keep-alive, and the generator never notices the quiet.
    """
    every = KEEPALIVE_SECONDS if every is None else every
    pending = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(frames))
            done, _ = await asyncio.wait({pending}, timeout=every)
            if not done:
                yield KEEPALIVE
                continue
            task, pending = pending, None
            try:
                frame = task.result()
            except StopAsyncIteration:
                return
            yield frame
    finally:
        # The client went away mid-wait. A generator cannot be closed while one of its
        # steps is running, so stop that step first, then close it, which runs its
        # finally blocks (the upstream socket is shut there).
        if pending is not None:
            pending.cancel()
            try:
                await pending
            except BaseException:
                pass
        await frames.aclose()


def _stream(frames) -> StreamingResponse:
    return StreamingResponse(
        _keepalive(frames),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Reverse proxies buffer by default, which turns a token-by-token stream
            # into one lump delivered at the end. Traefik does not, but this costs
            # nothing and the next proxy in front of this service might.
            "X-Accel-Buffering": "no",
        },
    )
