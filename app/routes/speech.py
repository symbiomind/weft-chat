"""
The speech relay: the route that gives a weft a voice.

One endpoint. The browser posts some text and the thread it belongs to; this looks up
the voice that thread was given, opens the *speech* provider's sealed bearer, asks it to
say the words, and hands back the audio.

**The browser does not choose the voice.** It names a weft, and the server resolves what
that weft was configured to sound like. That is not ceremony — if the request carried a
provider id and a voice, the endpoint would be a general-purpose proxy to any speech
provider the user owns, driven by whatever the page asked for. Naming the weft means the
only speech this route will ever buy is speech somebody deliberately configured.

**Two vault opens never happen here.** The chat provider's key is not touched; only the
speech provider's. They are different rows, usually different services, and there is no
path in this route that can reach the one it does not need.

**Not streamed, for now.** The honest end state is sentence-boundary detection in the
reply as it arrives, pipelining clips so the voice starts before the model has finished
thinking. That is real work and it is separate work. This waits for the whole reply and
speaks it once — enough to find out whether the room sounds better with voices in it,
which is the question actually being asked. See ``app/upstream.py::speak``.

**Clips are kept for a while**, so a reply can be heard again after a
reload, on another device, or with a paragraph that failed the first time filled in.
The key is the voice and the words, nothing else; see ``app/speech_cache.py``. A clip
that is kept is answered without opening the vault or calling anybody.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, deps, speech_cache, upstream, vault
from app.models import ProviderKind, ProviderSecret, User
from app.routes.wefts import owned_weft

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/speech",
    tags=["speech"],
    dependencies=[
        Depends(deps.require_db_healthy),
        Depends(deps.require_vault_available),
        Depends(deps.require_csrf),
    ],
)

#: One reply's worth of text. Long enough for anything a model says in a turn, short
#: enough that nobody can hand a speech provider a novel on this account's key.
MAX_SPEECH_CHARS = 8_000


class SpeechIn(BaseModel):
    #: The weft this text came from. The voice is looked up, never supplied.
    weft_id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)


@router.post("")
async def speak(
    body: SpeechIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> Response:
    """Say one reply in the voice its thread was given."""
    weft = await owned_weft(session, user, body.weft_id)

    if not any(ch.isalnum() for ch in body.text):
        # A sign-off emoji or a `---` on its own. The client no longer sends these, but
        # one that does would otherwise buy an error: Kokoro answers them with no
        # audio, OpenRouter with a 400 or a 502, and a 502 is asked twice.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="There are no words in that to say.",
        )

    if not weft.speaks():
        # 409 rather than 404: the thread exists, it simply has nothing to say things
        # with. The client checks `has_voice` before calling, so reaching this means the
        # voice was cleared in another tab — and "go and set one" is the useful answer.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{weft.name} has no voice. Give it one on its page.",
        )

    speaker = (
        await session.execute(
            select(ProviderSecret).where(
                ProviderSecret.id == weft.tts_provider_id,
                ProviderSecret.user_id == user.id,
            )
        )
    ).scalars().one_or_none()

    if speaker is None or speaker.kind is not ProviderKind.tts:
        # The foreign key is ON DELETE SET NULL, so a deleted speech provider normally
        # arrives as weft.speaks() being false. Getting here means something stranger —
        # worth a log line rather than a silent 409.
        logger.warning(
            "Weft %s names speech provider %s, which is missing or not a speech provider.",
            weft.id, weft.tts_provider_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The speech provider for that thread is no longer available.",
        )

    key = speech_cache.key_for(
        provider_id=speaker.id,
        base_url=speaker.base_url,
        model=weft.tts_model,
        voice=weft.tts_voice,
        text=body.text,
    )
    kept = await run_in_threadpool(speech_cache.fetch, user.id, key)
    if kept is not None:
        content_type, audio = kept
        return _audio(content_type, audio, cache="hit")

    try:
        secret = vault.open_secret(
            speaker.secret_ciphertext,
            speaker.secret_nonce,
            speaker.key_id,
            user_id=user.id,
            provider_id=speaker.id,
        )
    except vault.VaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This speech provider's stored token could not be opened on the server.",
        ) from exc

    try:
        content_type, audio = await upstream.speak(
            speaker.base_url,
            secret,
            model=weft.tts_model,
            voice=weft.tts_voice,
            text=body.text,
        )
    except upstream.UpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await run_in_threadpool(speech_cache.keep, user.id, key, content_type, audio)
    return _audio(content_type, audio, cache="miss")


def _audio(content_type: str, audio: bytes, *, cache: str) -> Response:
    return Response(
        content=audio,
        media_type=content_type,
        headers={
            # The audio is derived from a conversation. It must not sit in a shared
            # cache, and it must not be revalidated from disk on a machine somebody else
            # uses later.
            "Cache-Control": "no-store",
            # Whether this clip was kept from before. For anyone wondering why a replay
            # was instant, or why it was not.
            "X-Weft-Speech-Cache": cache,
        },
    )
