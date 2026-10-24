"""
Weft routes: the wefts on the Pick menu, and everything each one owns.

A weft is made by name ("Jarvis"), appears on the Pick menu at once, and is configured
on its own page: which engine answers it, its colours, its picture, how its thread is
kept, its voice and its request headers. Every one of those is a column on the weft's
own row, and nothing a provider screen does can reach them. Deleting a provider unbinds
the wefts it answered and leaves them otherwise whole — which is the whole reason this
shape replaced the old one, where a weft was a ticked (provider, model) pair and its
configuration died with the tick.

**Ownership is enforced by the query, as on the provider routes.** Every lookup is
``WHERE id = :id AND user_id = :me``, so another user's weft is not "found and then
refused" — it is not found.

**No route here opens a secret or makes an outbound call**, so none of them needs the
vault: the Pick menu and the weft page stay usable while it is down. The engine's model
list is fetched through ``GET /api/providers/{id}/models``, which does need it, and is
the only place that does.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import backgrounds, db, deps, sessions, upstream
from app.models import (
    MAX_TURNS,
    PickHistory,
    ProviderKind,
    User,
    Weft,
    new_id,
    weft_name_key,
)
from app.routes.providers import _read_capped, owned_provider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/wefts",
    tags=["wefts"],
    dependencies=[Depends(deps.require_db_healthy), Depends(deps.require_csrf)],
)

#: The only colour shape a weft stores. Six hex digits and nothing else: the client puts
#: these into CSS custom properties and into the donut's colour mixing, and a value that
#: was anything but a colour would be one more thing both of those had to defend against.
COLOUR_PATTERN = r"^#[0-9a-fA-F]{6}$"

#: A ceiling on one user's wefts. Nobody curates more than this by hand, and without it
#: a script could write an unbounded number of rows.
MAX_WEFTS_PER_USER = 200


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class HeaderRow(BaseModel):
    """One extra request header. Checked properly by ``upstream.clean_headers``; the
    lengths here only stop a body from being enormous before that runs."""

    name: str = Field(max_length=upstream.MAX_HEADER_NAME_CHARS * 2)
    value: str = Field(default="", max_length=upstream.MAX_HEADER_VALUE_CHARS * 2)


class WeftOut(BaseModel):
    """One weft, everything about it. The Pick menu and the weft page read the same
    shape, so a value set on one is never missing from the other."""

    id: str
    name: str

    #: The engine: which chat provider answers this weft, and with which model. Null for
    #: a weft with none yet. A weft whose provider was deleted has no provider but keeps
    #: the model id it was using, so its page can say what it was on.
    provider_id: str | None = None
    provider_name: str | None = None
    model_id: str | None = None

    #: Whether this weft has a voice, and the voice itself. The chat window only needs
    #: the bit; the weft page shows the rest.
    has_voice: bool = False
    tts_provider_id: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None

    #: Whether this weft's MCP door is set up: a key has been minted for its agent. Not
    #: whether an agent has come through it; the door's own route says that.
    has_door: bool = False

    #: Whether the window keeps this thread on the device showing it, how much of it
    #: goes upstream per turn, and how much of it the window holds at all. The chat view
    #: acts on all three every turn.
    history: PickHistory
    send_turns: int
    window_turns: int

    #: The pair this weft wears, as "#rrggbb", or null for the theme's own.
    colour_a: str | None = None
    colour_b: str | None = None

    #: The hash of this weft's background picture, or null for none. The window fetches
    #: the picture by it (GET /api/wefts/background/{hash}).
    background: str | None = None

    #: Where this thread is kept when `history` is `session`, the id the user typed (null
    #: for automatic), and the id actually in use.
    session_provider_id: str | None = None
    session_id: str | None = None
    session_key: str

    #: The extra headers this weft sends with its chat requests, in the order typed.
    headers: list[HeaderRow] = Field(default_factory=list)


class WeftIn(BaseModel):
    """Add a weft. The name is all it needs; the engine can be given now or later."""

    name: str = Field(min_length=1, max_length=64)
    provider_id: str | None = Field(default=None, max_length=36)
    model_id: str | None = Field(default=None, max_length=256)


class WeftPatch(BaseModel):
    """Rename a weft. The only thing about it that is not its own section."""

    name: str = Field(min_length=1, max_length=64)


class EngineIn(BaseModel):
    """Bind a weft to a provider and a model, or unbind it.

    Both or neither, like the voice: a provider with no model has nothing to ask for,
    and a model with no provider has nowhere to be asked.
    """

    provider_id: str | None = Field(default=None, max_length=36)
    model_id: str | None = Field(default=None, max_length=256)


class VoiceIn(BaseModel):
    """Give a weft a voice, or take it away. All three or none."""

    tts_provider_id: str | None = Field(default=None, max_length=36)
    tts_model: str | None = Field(default=None, max_length=128)
    tts_voice: str | None = Field(default=None, max_length=128)


class HistoryIn(BaseModel):
    """Set how a weft's conversation is kept, how much is sent, and how much is held.

    Every field is required rather than optional-with-a-default. This endpoint replaces
    the set, and a partial update would let a client that only knew about some of them
    silently reset the rest to defaults the user never chose.

    ``send_turns`` is allowed to exceed ``window_turns`` and is deliberately NOT clamped
    to it. The effective cap is the smaller of the two, but the number the user typed is
    the number that gets stored, so raising the window later finds their choice still
    waiting rather than quietly rewritten behind their back.
    """

    history: PickHistory

    #: Turns forwarded per message. 0 means "no cap beyond the window" — see
    #: Weft.send_turns for why that is not the same as "everything".
    send_turns: int = Field(ge=0, le=MAX_TURNS)

    #: Turns held on the device. At least 1: a window that holds nothing is not a
    #: window, it is the absence of one, and `history: memory` already says that.
    window_turns: int = Field(ge=1, le=MAX_TURNS)

    #: The session store (a `session` provider) for `history: session`. Required, and
    #: nullable: kept when `history` moves away from `session`, so moving back finds the
    #: store still chosen.
    session_provider_id: str | None = Field(max_length=36)

    #: A session id to join, or null for the weft's automatic one (app/sessions.py).
    session_id: str | None = Field(max_length=128, pattern=sessions.SESSION_ID_PATTERN)


class ColoursIn(BaseModel):
    """Dress a weft in a pair of colours, or send nulls to give it back to the theme.
    One without the other is refused — a gradient with one end is not a gradient."""

    colour_a: str | None = Field(default=None, pattern=COLOUR_PATTERN)
    colour_b: str | None = Field(default=None, pattern=COLOUR_PATTERN)


class HeadersIn(BaseModel):
    """Replace the extra headers a weft sends upstream. An empty list removes them.

    The whole list every time, like the history dials: the page holds the set, and a
    patch-one-row endpoint would need an identity for a row that has none but its name.
    """

    headers: list[HeaderRow] = Field(
        default_factory=list, max_length=upstream.MAX_EXTRA_HEADERS * 2
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def weft_out(weft: Weft) -> WeftOut:
    """The response shape. ``weft.provider`` and ``weft.door_key`` must already be loaded
    (``owned_weft`` and the list query both do), because an async session cannot
    lazy-load them here."""
    return WeftOut(
        id=weft.id,
        name=weft.name,
        provider_id=weft.provider_id,
        provider_name=weft.provider.name if weft.provider else None,
        model_id=weft.model_id,
        has_voice=weft.speaks(),
        tts_provider_id=weft.tts_provider_id,
        tts_model=weft.tts_model,
        tts_voice=weft.tts_voice,
        has_door=weft.door_key is not None,
        history=weft.history,
        send_turns=weft.send_turns,
        window_turns=weft.window_turns,
        colour_a=weft.colour_a,
        colour_b=weft.colour_b,
        background=weft.background,
        session_provider_id=weft.session_provider_id,
        session_id=weft.session_id,
        session_key=sessions.effective_session_id(weft),
        headers=[HeaderRow(name=name, value=value) for name, value in weft.headers or []],
    )


async def owned_weft(session: AsyncSession, user: User, weft_id: str) -> Weft:
    """Load one of *this user's* wefts, or 404.

    Public because the chat relay, the speech route and the session routes need exactly
    this lookup with exactly this authorization rule. The user_id clause in the WHERE is
    the authorization check, so a refactor cannot drop it and leave code that still
    looks correct.
    """
    weft = (
        await session.execute(
            select(Weft)
            .where(Weft.id == weft_id, Weft.user_id == user.id)
            .options(selectinload(Weft.provider), selectinload(Weft.door_key))
        )
    ).scalars().one_or_none()
    if weft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such weft.")
    return weft


def _clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A weft needs a name.",
        )
    return name


async def _refuse_a_taken_name(session: AsyncSession, user: User, name: str,
                               keep: str | None = None) -> None:
    """409 if another of this user's wefts has this name, naming it as IT spells it.

    The unique constraint is still the real guard (see ``_name_conflict``); this check
    is what gets the friendly answer, without poking at a session after a rollback.
    """
    query = select(Weft.name).where(
        Weft.user_id == user.id, Weft.name_key == weft_name_key(name)
    )
    if keep:
        query = query.where(Weft.id != keep)
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing is not None:
        raise _name_conflict(existing)


def _name_conflict(name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"You already have a weft called “{name}”.",
    )


async def _bind(session: AsyncSession, user: User, weft: Weft,
                provider_id: str | None, model_id: str | None) -> None:
    """Point a weft at an engine, or at nothing. Checks, then assigns."""
    model_id = model_id.strip() if model_id else None
    if not provider_id and not model_id:
        weft.provider_id = None
        weft.model_id = None
        weft.provider = None
        return
    if not (provider_id and model_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An engine is a provider and a model — both, or neither to unbind it.",
        )
    # Ownership: without it a weft could be answered on somebody else's key.
    provider = await owned_provider(session, user, provider_id)
    if provider.kind is not ProviderKind.llm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That provider is not a chat provider.",
        )
    weft.provider_id = provider.id
    weft.provider = provider
    weft.model_id = model_id


# ---------------------------------------------------------------------------
# The wefts themselves
# ---------------------------------------------------------------------------

@router.get("", response_model=list[WeftOut])
async def list_wefts(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> list[WeftOut]:
    """Every weft this user has, by name. This is the Pick menu.

    Reads no secrets and makes no outbound call, so opening the chat costs one indexed
    query, not a round trip to every gateway the user has configured.
    """
    wefts = (
        await session.execute(
            select(Weft)
            .where(Weft.user_id == user.id)
            .options(selectinload(Weft.provider), selectinload(Weft.door_key))
            .order_by(Weft.name_key)
        )
    ).scalars().all()
    return [weft_out(w) for w in wefts]


@router.post("", response_model=WeftOut, status_code=status.HTTP_201_CREATED)
async def create_weft(
    body: WeftIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Add a weft by name. It is on the Pick menu from this moment, engine or not."""
    name = _clean_name(body.name)
    await _refuse_a_taken_name(session, user, name)

    count = len((await session.execute(
        select(Weft.id).where(Weft.user_id == user.id)
    )).scalars().all())
    if count >= MAX_WEFTS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That is {MAX_WEFTS_PER_USER} wefts already. Delete one first.",
        )

    # The id is generated here because the automatic session id is built from it.
    weft = Weft(id=new_id(), user_id=user.id, name=name, name_key=weft_name_key(name))
    weft.auto_session_id = sessions.derived_session_id(user.username, weft.id)
    weft.provider = None
    await _bind(session, user, weft, body.provider_id, body.model_id)

    session.add(weft)
    try:
        await session.commit()
    except IntegrityError as exc:
        # The unique constraint is the real guard: two tabs adding "Jarvis" at once
        # would both pass a pre-check.
        await session.rollback()
        raise _name_conflict(name) from exc

    return weft_out(await owned_weft(session, user, weft.id))


@router.get("/{weft_id}", response_model=WeftOut)
async def get_weft(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    return weft_out(await owned_weft(session, user, weft_id))


@router.patch("/{weft_id}", response_model=WeftOut)
async def rename_weft(
    weft_id: str,
    body: WeftPatch,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Rename a weft. Its thread stays put: the session id does not follow the name."""
    weft = await owned_weft(session, user, weft_id)
    name = _clean_name(body.name)
    await _refuse_a_taken_name(session, user, name, keep=weft.id)
    weft.name = name
    weft.name_key = weft_name_key(name)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _name_conflict(name) from exc
    return weft_out(await owned_weft(session, user, weft_id))


@router.delete("/{weft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_weft(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> Response:
    """Delete a weft and everything it owns. The only route that can.

    Its picture goes too once nothing else wears it. A thread kept in a session store
    is not touched: that store is upstream, and its contents are not ours to delete.
    """
    weft = await owned_weft(session, user, weft_id)
    await session.delete(weft)
    await session.commit()
    await backgrounds.sweep(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# One weft's sections
# ---------------------------------------------------------------------------

@router.put("/{weft_id}/engine", response_model=WeftOut)
async def set_engine(
    weft_id: str,
    body: EngineIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Say which provider and model answer this weft, or that nothing does.

    Everything else about the weft stays as it was. That is the point of the shape: the
    same weft on a new engine keeps its colours, voice, picture and thread.
    """
    weft = await owned_weft(session, user, weft_id)
    await _bind(session, user, weft, body.provider_id, body.model_id)
    await session.commit()
    return weft_out(weft)


@router.put("/{weft_id}/history", response_model=WeftOut)
async def set_history(
    weft_id: str,
    body: HistoryIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Say how this weft is kept, how much is sent, and how much is held.

    Setting ``history`` to ``memory`` does NOT reach out and delete anything. The stored
    copy lives in a browser this service cannot see — the client erases its own copy
    when it applies this.
    """
    weft = await owned_weft(session, user, weft_id)

    if body.session_provider_id is not None:
        # Ownership, as for a voice: without it a weft could keep its thread in somebody
        # else's store, on their token.
        store = await owned_provider(session, user, body.session_provider_id)
        if store.kind is not ProviderKind.session:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That provider is not a session store.",
            )
    elif body.history is PickHistory.session:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Keeping a thread in a session store needs a session store chosen.",
        )

    weft.history = body.history
    weft.send_turns = body.send_turns
    weft.window_turns = body.window_turns
    weft.session_provider_id = body.session_provider_id
    weft.session_id = body.session_id
    await session.commit()
    return weft_out(weft)


@router.put("/{weft_id}/colours", response_model=WeftOut)
async def set_colours(
    weft_id: str,
    body: ColoursIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Set the pair of colours this weft wears, or clear it back to the theme's.
    Stored lowercase, so "#ABCDEF" and "#abcdef" are the same colour twice."""
    weft = await owned_weft(session, user, weft_id)

    if (body.colour_a is None) != (body.colour_b is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Colours come as a pair — both, or neither to go back to the theme's.",
        )

    weft.colour_a = body.colour_a.lower() if body.colour_a else None
    weft.colour_b = body.colour_b.lower() if body.colour_b else None
    await session.commit()
    return weft_out(weft)


@router.put("/{weft_id}/headers", response_model=WeftOut)
async def set_headers(
    weft_id: str,
    body: HeadersIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Set the extra HTTP headers this weft sends with its chat requests.

    Not secrets, and treated as not secrets: they are shown back in the clear. The
    names that carry a credential, or describe the request itself, are refused (see
    ``upstream.RESERVED_HEADERS``), so a header can add to what Weft sends and never
    replace it.
    """
    weft = await owned_weft(session, user, weft_id)

    try:
        cleaned = upstream.clean_headers([(row.name, row.value) for row in body.headers])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    weft.headers = cleaned or None
    await session.commit()
    return weft_out(weft)


@router.put("/{weft_id}/voice", response_model=WeftOut)
async def set_voice(
    weft_id: str,
    body: VoiceIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Give this weft a voice, or clear it. All three fields or none."""
    weft = await owned_weft(session, user, weft_id)

    if not any((body.tts_provider_id, body.tts_model, body.tts_voice)):
        weft.tts_provider_id = None
        weft.tts_model = None
        weft.tts_voice = None
        await session.commit()
        return weft_out(weft)

    if not all((body.tts_provider_id, body.tts_model, body.tts_voice)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A voice needs a speech provider, a model and a voice — or none of the three.",
        )

    # Ownership again, on the speech provider this time. Without it a user could have
    # the server spend somebody else's key on their behalf.
    speaker = await owned_provider(session, user, body.tts_provider_id)
    if speaker.kind is not ProviderKind.tts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That provider is not a speech provider.",
        )

    weft.tts_provider_id = speaker.id
    weft.tts_model = body.tts_model
    weft.tts_voice = body.tts_voice
    await session.commit()
    return weft_out(weft)


# ---------------------------------------------------------------------------
# Background pictures
# ---------------------------------------------------------------------------

@router.get("/background/{digest}")
async def get_background(
    digest: str = Path(pattern=backgrounds.HASH_PATTERN),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> FileResponse:
    """A background picture — only to someone with a weft wearing it.

    A picture that exists but is not the caller's gets the same 404 as one that does not
    exist, so the route cannot be used to ask whether somebody else has uploaded a given
    image. Cacheable forever, privately: the name is the hash of the bytes.
    """
    owned = (
        await session.execute(
            select(Weft.id)
            .where(Weft.user_id == user.id, Weft.background == digest)
            .limit(1)
        )
    ).scalar_one_or_none()

    path = backgrounds.path_for(digest)
    if owned is None or path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such picture.")

    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.put("/{weft_id}/background", response_model=WeftOut)
async def set_background(
    weft_id: str,
    request: Request,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Give this weft a background picture, replacing any it had.

    The body is the picture itself, not a form, so the size cap bites while it streams.
    The content type must say it is an image — and SVG, which is one, is refused,
    because it is markup that can carry script. What is stored is never what was sent;
    see app/backgrounds.py.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Send the picture itself, as a JPEG, PNG, WebP or GIF.",
        )

    # Ownership before the upload is read: a stranger's request should cost a lookup,
    # not twenty megabytes of decoding.
    weft = await owned_weft(session, user, weft_id)

    data = await _read_capped(request, backgrounds.MAX_UPLOAD_BYTES)
    try:
        encoded = await run_in_threadpool(backgrounds.encode, data)
    except backgrounds.NotAPicture as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    digest = await run_in_threadpool(backgrounds.store, encoded)

    weft.background = digest
    await session.commit()
    await backgrounds.sweep(session)
    return weft_out(weft)


@router.delete("/{weft_id}/background", response_model=WeftOut)
async def clear_background(
    weft_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> WeftOut:
    """Take this weft's picture away. The file goes too, once nothing else wears it."""
    weft = await owned_weft(session, user, weft_id)
    weft.background = None
    await session.commit()
    await backgrounds.sweep(session)
    return weft_out(weft)

