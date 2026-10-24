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
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments, db, deps, eliza, upstream, vault
from app.attachments import MAX_PER_MESSAGE, Attachment
from app.models import User, utcnow
from app.routes.providers import owned_provider
from app.routes.sessions import keep_exchange, resolve_store
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


class Message(BaseModel):
    #: Only the three roles the OpenAI protocol defines for input. A free string here
    #: would be forwarded verbatim to somebody else's parser.
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)
    #: Pictures on this message, by the hash PUT /api/attachments returned
    #:. Only the newest user message's go upstream as pictures; see
    #: wire_messages.
    attachments: list[Attachment] = Field(default_factory=list, max_length=MAX_PER_MESSAGE)
    #: Spoken through push to talk rather than typed. The weft is told
    #: with a marker when the user wants it to be; see wire_messages.
    spoken: bool = False


async def wire_messages(messages: list[Message], user_id: str, *,
                        tell_spoken: bool = True) -> list[dict]:
    """The conversation as the provider receives it.

    Every message's text gets its pictures' marker in front (``[image: photo.jpg]``),
    so a reader that only keeps text, like ind-bridge's session storage, still knows a
    picture was there. A message with pictures and no words would otherwise be no text
    at all, and the bridge does not store a turn with no text.

    Only the NEWEST message, when it is the user's, carries its pictures as pictures:
    a list of content parts, the images first and the words after, each image a
    ``data:`` URI read from this user's own attachments. Earlier turns are their marker
    alone. A being looks at a picture once and remembers what it saw, and every later
    turn would otherwise pay the picture's tokens again.

    A spoken message gets ``[spoken]`` after any picture markers, when ``tell_spoken``
    (the user's own setting) says the weft should know.
    """
    wire = []
    last = len(messages) - 1
    for index, message in enumerate(messages):
        names = [a.name for a in message.attachments]
        text = attachments.with_marker(
            message.content, names, message.spoken and tell_spoken)
        if index != last or message.role != "user" or not message.attachments:
            wire.append({"role": message.role, "content": text})
            continue

        parts = []
        for attachment in message.attachments:
            uri = await run_in_threadpool(attachments.data_uri, user_id, attachment.hash)
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
    pictures = [
        {"hash": a.hash, "name": attachments.clean_name(a.name)} for a in last.attachments
    ] if last.role == "user" else []
    spoken = last.role == "user" and last.spoken
    user_text = (
        attachments.with_marker(
            last.content, [p["name"] for p in pictures], spoken and user.stt_tell_weft)
        if last.role == "user" else None
    )
    # The store keeps the pictures as references, never the pictures themselves, and
    # remembers a spoken message as spoken even when the weft was not told.
    user_body = {
        **({"attachments": pictures} if pictures else {}),
        **({"spoken": True} if spoken else {}),
    } or None
    sent_at = utcnow().isoformat()

    payload = {
        "model": model_id,
        "messages": await wire_messages(
            body.messages, user.id, tell_spoken=user.stt_tell_weft),
        "stream": True,
    }

    try:
        deltas = await upstream.stream_chat(
            provider.base_url, secret, payload, extra_headers=weft.headers
        )
    except upstream.UpstreamError as exc:
        # Nothing has been sent yet, so this can still be an honest status code.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    async def body_stream():
        reply: list[str] = []
        try:
            try:
                async for delta in deltas:
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
            if store is not None and user_text and reply:
                notice = await keep_exchange(
                    store, user_text, "".join(reply), model=model_id, sent_at=sent_at,
                    user_body=user_body,
                )
                if notice:
                    yield _frame({"error": notice})
        finally:
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


def _stream(frames) -> StreamingResponse:
    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Reverse proxies buffer by default, which turns a token-by-token stream
            # into one lump delivered at the end. Traefik does not, but this costs
            # nothing and the next proxy in front of this service might.
            "X-Accel-Buffering": "no",
        },
    )
