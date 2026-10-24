"""
Push to talk: the user's speech-to-text settings, and the relay that
turns a recording into words.

**One provider per user, not per pick.** It is the user's voice being heard, and that
does not change with who is listening. The settings live on the user row; see
``User.stt_provider_id`` and its neighbours.

**The audio is relayed, never kept.** The browser posts the recording, the server opens
the provider's sealed bearer and posts it on, and the words come back. Nothing about the
recording is written anywhere.

**The browser does not choose the provider.** It posts audio; the server uses whichever
provider the user chose in Settings. A request naming a provider would make this route a
general-purpose proxy to any endpoint the user owns.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, deps, upstream
from app.models import ProviderKind, ProviderSecret, User
from app.routes.providers import _open_or_500, _read_capped, owned_provider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/stt",
    tags=["stt"],
    dependencies=[
        Depends(deps.require_db_healthy),
        Depends(deps.require_vault_available),
        Depends(deps.require_csrf),
    ],
)

#: What is sent as `model` when the user has not chosen one. OpenAI's name for whisper,
#: which servers that ignore the field (faster-whisper ones often do) accept as well.
DEFAULT_MODEL = "whisper-1"

_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class SttOut(BaseModel):
    #: The chosen provider, or null for none.
    provider_id: str | None
    provider_name: str | None
    model: str | None
    #: A language hint such as "en", or null to let the provider detect it.
    language: str | None
    #: Send the transcript as soon as the user lets go (fire and forget).
    send_on_release: bool
    #: Tell the weft a message was spoken.
    tell_weft: bool
    #: Whether push to talk can be used: a provider is chosen and still is one.
    available: bool


class SttIn(BaseModel):
    """The whole settings block, replaced at once, as the Settings form sends it."""

    provider_id: str | None = Field(default=None, max_length=36)
    model: str | None = Field(default=None, max_length=256)
    language: str | None = Field(default=None, max_length=16)
    send_on_release: bool = False
    tell_weft: bool = True

    @field_validator("model", "language", "provider_id")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("language")
    @classmethod
    def _language_shape(cls, value: str | None) -> str | None:
        if value is not None and not _LANGUAGE.match(value):
            raise ValueError("a language code such as 'en' or 'pt-BR'")
        return value


class TranscriptOut(BaseModel):
    text: str


class ModelsOut(BaseModel):
    models: list[str]


async def _chosen(session: AsyncSession, user: User) -> ProviderSecret | None:
    """The user's speech-to-text provider, if one is chosen and still is one."""
    if not user.stt_provider_id:
        return None
    provider = (
        await session.execute(
            select(ProviderSecret).where(
                ProviderSecret.id == user.stt_provider_id,
                ProviderSecret.user_id == user.id,
            )
        )
    ).scalars().one_or_none()
    if provider is None or provider.kind is not ProviderKind.stt:
        return None
    return provider


def _out(user: User, provider: ProviderSecret | None) -> SttOut:
    return SttOut(
        provider_id=provider.id if provider else None,
        provider_name=provider.name if provider else None,
        model=user.stt_model,
        language=user.stt_language,
        send_on_release=user.stt_send_on_release,
        tell_weft=user.stt_tell_weft,
        available=provider is not None,
    )


@router.get("", response_model=SttOut)
async def get_settings(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> SttOut:
    """The user's push-to-talk settings. The window asks on mount, to know whether to
    show the 🎤 petal at all."""
    return _out(user, await _chosen(session, user))


@router.put("", response_model=SttOut)
async def set_settings(
    body: SttIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> SttOut:
    """Replace the user's push-to-talk settings."""
    provider = None
    if body.provider_id:
        provider = await owned_provider(session, user, body.provider_id)
        if provider.kind is not ProviderKind.stt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That provider is not a speech-to-text provider.",
            )

    user.stt_provider_id = provider.id if provider else None
    user.stt_model = body.model
    user.stt_language = body.language
    user.stt_send_on_release = body.send_on_release
    user.stt_tell_weft = body.tell_weft
    await session.commit()
    return _out(user, provider)


@router.get("/models", response_model=ModelsOut)
async def list_models(
    provider_id: str = Query(min_length=1, max_length=36),
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> ModelsOut:
    """What a speech-to-text provider says it can run. Empty when it will not say: the
    user can still type a model name, and many servers ignore it anyway."""
    provider = await owned_provider(session, user, provider_id)
    if provider.kind is not ProviderKind.stt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That provider is not a speech-to-text provider.",
        )
    secret = _open_or_500(provider, user)
    try:
        models = [row["id"] for row in await upstream.fetch_models(provider.base_url, secret)]
    except upstream.UpstreamError:
        models = []
    return ModelsOut(models=models)


@router.post("/transcribe", response_model=TranscriptOut)
async def transcribe(
    request: Request,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> TranscriptOut:
    """Turn one recording into words, with the user's own provider.

    The body is the recording itself, with the content type the browser recorded it in
    (``audio/webm`` from Chrome, ``audio/mp4`` from Safari).
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Send the recording itself, with an audio content type.",
        )

    # Before the upload is read: no provider means a lookup, not 25 MB of buffering.
    provider = await _chosen(session, user)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Push to talk needs a speech-to-text provider. Choose one in Settings.",
        )

    audio = await _read_capped(request, upstream.MAX_TRANSCRIBE_BYTES, noun="recording")
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="That recording is empty.",
        )

    secret = _open_or_500(provider, user)
    try:
        text = await upstream.transcribe(
            provider.base_url, secret,
            audio=audio, content_type=content_type,
            model=user.stt_model or DEFAULT_MODEL, language=user.stt_language,
        )
    except upstream.UpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return TranscriptOut(text=text)
