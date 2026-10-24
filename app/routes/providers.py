"""
Provider routes: the user's own upstream endpoints, their sealed bearer tokens, and the
live model list behind each one. The wefts that use them are in app/routes/wefts.py.

**The one rule that shapes every handler here: the plaintext secret never leaves the
server.** Not for the owner, not for an admin, not "just to prefill the edit form". A
read returns ``hint`` — the last four characters — and nothing else. That is why the
edit form leaves the token field blank rather than populated: there is no endpoint that
could populate it, by construction, and ``tests/test_openapi_spec.py`` fails if a
response schema ever grows a field that could.

**Why the model list is fetched live and never stored.** It is somebody else's
catalogue. A cached copy is wrong the moment they add a model, and the failure — "I
added it upstream and Weft can't see it" — looks like a Weft bug. What we persist is
which model a weft is bound to, which is ours and does not go stale.

**Ownership is enforced by the query, not by a check after the fact.** Every lookup is
``WHERE id = :id AND user_id = :me``, so another user's provider is not "found and then
refused" — it is not found. A 403 would confirm the id exists, and these ids appear in
nothing a stranger can see, so there is no reason to start leaking them now.

**Providers are bindings, not parents**. Editing a provider does not
touch the wefts bound to it, even when the ``base_url`` changes to a completely
different gateway: they then ask for model ids that endpoint may not have, and the user
finds out when they use one. Deleting a provider unbinds its wefts and leaves them
otherwise whole. Nothing here can delete a weft or touch its configuration.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import db, deps, upstream, vault
from app.models import ProviderKind, ProviderSecret, User, Weft, new_id

logger = logging.getLogger(__name__)

#: Router-level dependencies rather than per-route ones. A route added here a year from
#: now inherits the CSRF check, the database gate and the vault gate without anyone
#: having to remember — which is the failure this shape exists to prevent, since a
#: forgotten dependency produces no error, just an unguarded endpoint.
_GUARDS = [
    Depends(deps.require_db_healthy),
    Depends(deps.require_vault_available),
    Depends(deps.require_csrf),
]

router = APIRouter(prefix="/api/providers", tags=["providers"], dependencies=_GUARDS)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProviderIn(BaseModel):
    """The Add form. ``secret`` is optional because a gateway on the private network
    often needs no bearer at all, and requiring one would mean inventing a placeholder
    that then gets sent upstream."""

    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=upstream.MAX_URL_CHARS)
    secret: str = Field(default="", max_length=vault.MAX_SECRET_CHARS)

    #: What this endpoint is for. Settable on create only — see ProviderPatch.
    kind: ProviderKind = ProviderKind.llm


class ProviderPatch(BaseModel):
    """The Edit form. Every field optional, and ``secret`` has three meanings:

    * absent / ``null`` — leave the stored token alone. This is what the form sends
      when the user did not type in the blank token field, and it is why an edit does
      not require re-entering a secret nobody can read back.
    * ``""`` — clear it. The provider then sends no Authorization header.
    * anything else — replace it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=upstream.MAX_URL_CHARS)
    secret: str | None = Field(default=None, max_length=vault.MAX_SECRET_CHARS)

    # NOTE there is no `kind` here, deliberately. Flipping an existing provider from llm
    # to tts would leave the wefts bound to it pointing at a thing that no longer answers
    # /chat/completions, and any weft using it for a voice pointing at a thing that no
    # longer answers /audio/speech. Delete and re-add is one more click and cannot
    # produce a half-valid row.


class ProviderOut(BaseModel):
    id: str
    name: str
    base_url: str
    kind: ProviderKind
    #: Last four characters of the token, or null when there is no token (or it is too
    #: short to hint at without giving it away). The only thing we ever say about it.
    hint: str | None
    has_secret: bool
    #: How many wefts this provider answers. Lets the Providers list say something
    #: useful without a second request per row.
    weft_count: int


class ModelOut(BaseModel):
    id: str
    owned_by: str
    #: The names of this user's wefts already bound to this model on this provider, so
    #: the engine picker can say "Jarvis uses this" without a second request.
    wefts: list[str] = Field(default_factory=list)


class ModelsOut(BaseModel):
    provider_id: str
    provider_name: str
    models: list[ModelOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _provider_out(provider: ProviderSecret, weft_count: int) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        kind=provider.kind,
        hint=provider.hint,
        # Not `bool(provider.hint)`: a short secret is stored but gets no hint, and
        # reporting that as "no token" would have the user re-enter a working one.
        has_secret=bool(provider.secret_ciphertext),
        weft_count=weft_count,
    )


async def owned_provider(
    session: AsyncSession, user: User, provider_id: str
) -> ProviderSecret:
    """Load one of *this user's* providers, or 404.

    Public because the chat relay needs exactly this lookup with exactly this
    authorization rule. Two copies of an ownership check is one copy too many — the day
    they drift is the day one of them stops scoping by user.

    The user_id clause is the authorization check. Doing it in the WHERE rather than as
    an ``if provider.user_id != user.id`` after the fetch means a future refactor cannot
    drop the check while leaving code that still looks correct.
    """
    provider = (
        await session.execute(
            select(ProviderSecret).where(
                ProviderSecret.id == provider_id,
                ProviderSecret.user_id == user.id,
            )
        )
    ).scalars().one_or_none()

    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such provider.")
    return provider


async def _weft_count(session: AsyncSession, provider_id: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Weft).where(Weft.provider_id == provider_id)
        )
    ).scalar_one()


def _validated_url(raw: str) -> str:
    try:
        return upstream.normalise_base_url(raw)
    except ValueError as exc:
        # 422 rather than 400: this is a field-level validation failure, the same class
        # pydantic reports, and the client renders it next to the field.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def _seal_or_503(secret: str, *, user_id: str, provider_id: str) -> tuple[bytes, bytes, str]:
    try:
        return vault.seal(secret, user_id=user_id, provider_id=provider_id)
    except vault.VaultError as exc:
        logger.error("Could not seal a provider secret: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The provider vault is not usable on this server.",
        ) from exc


def _open_or_500(provider: ProviderSecret, user: User) -> str:
    """Decrypt one provider's bearer, or fail with a message that says nothing useful
    to anyone but the operator reading the log."""
    try:
        return vault.open_secret(
            provider.secret_ciphertext,
            provider.secret_nonce,
            provider.key_id,
            user_id=user.id,
            provider_id=provider.id,
        )
    except vault.VaultError as exc:
        # vault.open_secret has already logged which of the several reasons applies.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This provider's stored token could not be opened on the server.",
        ) from exc


def _refuse_session_store(provider: ProviderSecret) -> None:
    """A session store has no models to list: it keeps threads, it does not
    answer them. Refused rather than attempted, because its /models would 404 and the
    message would blame the URL."""
    if provider.kind is ProviderKind.session:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That provider is a session store. It has no models to choose from.",
        )


async def _read_capped(request: Request, limit: int, noun: str = "picture") -> bytes:
    """The request body, refused the moment it passes `limit`.

    Counted as it streams in rather than after it has been buffered: a Content-Length
    can lie or be absent, and checking the size of something already in memory is
    checking too late.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit, noun)

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large(limit, noun)
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large(limit: int, noun: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"That {noun} is over {limit // (1024 * 1024)} MB.",
    )


def _name_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="You already have a provider with that name.",
    )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ProviderOut])
async def list_providers(
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> list[ProviderOut]:
    """Every provider this user owns, oldest first, each with how many wefts it answers.

    ``selectinload`` on the wefts turns what would be one query per row into two
    queries total. With a handful of providers that is not a performance concern; it is
    here so the N+1 never gets written in the first place.
    """
    providers = (
        await session.execute(
            select(ProviderSecret)
            .where(ProviderSecret.user_id == user.id)
            .options(selectinload(ProviderSecret.wefts))
            .order_by(ProviderSecret.created_at)
        )
    ).scalars().all()

    return [_provider_out(p, len(p.wefts)) for p in providers]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderIn,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> ProviderOut:
    """Add a provider.

    The row id is generated here rather than left to the column default because the
    vault's associated data binds the ciphertext to it — the id has to exist before the
    secret can be sealed. See ``app/vault.py`` for why that binding is worth the
    awkwardness.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A provider name is required.",
        )

    base_url = _validated_url(body.base_url)

    # new_id() explicitly, NOT the column default. The column default is applied by
    # SQLAlchemy at INSERT time, so `provider.id` would still be None here — and the
    # vault would seal the secret against the associated data "…:None" while every
    # later read computed it from the real id. Nothing would fail until somebody opened
    # the models screen, and then it would look like a corrupt row.
    provider = ProviderSecret(
        id=new_id(), user_id=user.id, name=name, base_url=base_url
    )
    ciphertext, nonce, key_id = _seal_or_503(
        body.secret, user_id=user.id, provider_id=provider.id
    )
    provider.secret_ciphertext = ciphertext
    provider.secret_nonce = nonce
    provider.key_id = key_id
    provider.hint = vault.hint_for(body.secret)
    provider.kind = body.kind

    session.add(provider)
    try:
        await session.commit()
    except IntegrityError as exc:
        # The unique constraint is the real guard, not a pre-check: two tabs submitting
        # the same name at once would both pass a pre-check and only one would land.
        await session.rollback()
        raise _name_conflict() from exc

    return _provider_out(provider, 0)


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    body: ProviderPatch,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> ProviderOut:
    """Edit a provider. Omitting ``secret`` leaves the stored token untouched."""
    provider = await owned_provider(session, user, provider_id)

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A provider name is required.",
            )
        provider.name = name

    if body.base_url is not None:
        provider.base_url = _validated_url(body.base_url)

    if body.secret is not None:
        # A fresh nonce on every re-seal, from vault.seal. Re-using this row's existing
        # nonce with the same key would be the GCM catastrophe, so the only safe way to
        # "update the secret" is to encrypt it from scratch.
        ciphertext, nonce, key_id = _seal_or_503(
            body.secret, user_id=user.id, provider_id=provider.id
        )
        provider.secret_ciphertext = ciphertext
        provider.secret_nonce = nonce
        provider.key_id = key_id
        provider.hint = vault.hint_for(body.secret)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _name_conflict() from exc

    return _provider_out(provider, await _weft_count(session, provider.id))


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> Response:
    """Remove a provider. The wefts it answered stay, unbound.

    The database's ON DELETE SET NULL does the unbinding. A weft with no engine is
    still on the Pick menu with everything it owns, waiting for a new one.
    """
    provider = await owned_provider(session, user, provider_id)
    await session.delete(provider)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@router.get("/{provider_id}/models", response_model=ModelsOut)
async def list_models(
    provider_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> ModelsOut:
    """Fetch this provider's live model list, and say which wefts use which.

    This is the only route in the service that opens a sealed secret, and the only one
    that makes an outbound request. Both facts are worth keeping true: if a second
    route needs either, that is the moment to ask whether it should go through here.
    """
    provider = await owned_provider(session, user, provider_id)
    _refuse_session_store(provider)

    secret = _open_or_500(provider, user)

    try:
        rows = await upstream.fetch_models(provider.base_url, secret)
    except upstream.UpstreamError as exc:
        # 502, because this service is fine — the thing it was asked to talk to is not.
        # The message is the upstream's own, which is what the person who configured
        # that endpoint needs to see.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    bound = (
        await session.execute(
            select(Weft.model_id, Weft.name)
            .where(Weft.provider_id == provider.id)
            .order_by(Weft.name_key)
        )
    ).all()
    users: dict[str, list[str]] = {}
    for model_id, name in bound:
        users.setdefault(model_id, []).append(name)

    models = [
        ModelOut(id=row["id"], owned_by=row["owned_by"], wefts=users.get(row["id"], []))
        for row in rows
    ]
    # Models some weft already uses first, then alphabetical. A real provider answered
    # with 445 models, and "which one is Jarvis on" should not be buried in them.
    models.sort(key=lambda m: (not m.wefts, m.id.lower()))

    return ModelsOut(provider_id=provider.id, provider_name=provider.name, models=models)


class VoicesOut(BaseModel):
    provider_id: str
    provider_name: str
    #: The speech models this endpoint offers, from /models. Usually one ("kokoro").
    models: list[str]
    #: From /audio/voices. EMPTY IS NOT AN ERROR — OpenAI itself has no voices endpoint,
    #: so a client that gets an empty list offers a text field instead of a dropdown
    #: rather than refusing to configure the provider.
    voices: list[str]


@router.get("/{provider_id}/voices", response_model=VoicesOut)
async def list_voices(
    provider_id: str,
    user: User = Depends(deps.active_user),
    session: AsyncSession = Depends(db.get_session),
) -> VoicesOut:
    """What a speech provider can say, and in whose voice.

    Two upstream calls, because the OpenAI speech protocol splits them: ``/models`` names
    the engine and ``/audio/voices`` names the voice, and ``/audio/speech`` wants both.
    """
    provider = await owned_provider(session, user, provider_id)

    if provider.kind is not ProviderKind.tts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That provider is not a speech provider.",
        )

    secret = _open_or_500(provider, user)

    try:
        models = [row["id"] for row in await upstream.fetch_models(provider.base_url, secret)]
    except upstream.UpstreamError:
        # A speech server that will not enumerate its models is still usable — the user
        # types the model name. Refusing here would make a working provider
        # unconfigurable.
        models = []

    try:
        voices = await upstream.fetch_voices(provider.base_url, secret)
    except upstream.UpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return VoicesOut(
        provider_id=provider.id,
        provider_name=provider.name,
        models=models,
        voices=voices,
    )
