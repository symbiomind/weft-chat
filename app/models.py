"""
SQLAlchemy 2.0 models for weft-service.

Design notes that are security decisions rather than style choices:

**IDs are UUID4 strings, not autoincrement integers.** Sequential ids leak population
size and let anyone who sees one id guess the neighbours — which matters for
``/api/users/{id}`` and for the invite table. The cost is 36 bytes instead of 8, which
is irrelevant at this scale.

**Every timestamp goes through ``UTCDateTime``.** sqlite has no native timezone type
and hands back naive datetimes, so a plain ``DateTime(timezone=True)`` column silently
returns a *naive* value on read. Comparing that against an aware ``datetime.now(UTC)``
raises TypeError at best and compares wrong at worst — and every one of these columns
is load-bearing for expiry. The TypeDecorator below normalises both directions so the
application only ever sees aware UTC, on sqlite today and on postgres later.

**No secret is stored in plaintext.** Passwords are argon2id hashes; refresh and invite
tokens are SHA-256 digests of high-entropy random values; provider secrets are AES-GCM
ciphertext with the nonce beside them. The only reversible thing in this schema is the
provider secret, and only with a key that lives in the environment.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Timezone-aware UTC now. The only clock this service reads.

    Every expiry check in the codebase compares against this, so there is exactly one
    place to look when a token expires at the wrong moment.
    """
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """A DateTime that is always aware-UTC in Python, whatever the backend does.

    sqlite stores datetimes as strings and returns them naive. Postgres with
    ``timezone=True`` returns them aware. Without this decorator the same expiry
    comparison would work on one and raise ``TypeError: can't compare offset-naive and
    offset-aware datetimes`` on the other — the sort of difference that shows up only
    after the postgres migration, in the auth path, in production.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # A naive value reaching the DB is a bug upstream, but assuming UTC is
            # strictly better than storing it with an implied local offset.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Role(str, PyEnum):
    """Three levels, and the gap between them is the whole authorization model.

    owner  — exactly one at bootstrap; the only role that may grant `admin` or change
             anyone's role. The single privilege-escalation path in the service.
    admin  — may create and deactivate users and mint `user` invites. May NOT change
             roles, and may not read anyone's vault.
    user   — owns their own providers and nothing else.
    """

    owner = "owner"
    admin = "admin"
    user = "user"


class ProviderKind(str, PyEnum):
    """What a stored endpoint is for.

    `llm` is an OpenAI-compatible chat/models endpoint. `tts` is a speech endpoint
    — the same `/v1`-shaped surface, but answering `/audio/speech` instead
    of `/chat/completions`. Both live in this one table because they are the same thing
    from the vault's point of view: somebody else's URL and somebody else's key.

    `session` is a memory-mcp-ce server whose `/v1/sessions` API stores conversations
    (2026-10-04). Same table for the same reason: a URL and a key that are somebody
    else's, sealed in the vault, opened only by the service.

    An enum rather than a free string so the picker can filter on it without a
    magic-value convention, and so adding `stt` later is a migration rather than a
    convention nobody wrote down."""

    llm = "llm"
    tts = "tts"
    session = "session"
    #: Speech to text: answers ``/audio/transcriptions``. Push to talk
    #: sends the user's recording here. One per user, chosen in Settings.
    stt = "stt"


class PickHistory(str, PyEnum):
    """Whether a pick keeps a copy of its conversation on the device showing it.

    `memory` is the default and means what Weft has always done: a reload empties the
    window. `local` means the browser keeps a scrollback so a refresh does not wipe what
    you were reading.

    `session` (2026-10-04) means the conversation lives in a session store: a
    memory-mcp-ce server the user added as a `session` provider. The service relays the
    thread to and from it and keeps nothing itself. The service still stores no conversation, because
    relaying is not storing: the store is upstream, like the gateway, and every device
    that opens the pick sees the same thread because the store has it.

    None of these values stores anything HERE. The service keeps no
    conversation, and `local` is a copy in one browser that never reaches this side of
    the glass. What is stored in this column is the *choice*, which is furniture — the
    same argument the voice columns below already make.
    """

    memory = "memory"
    local = "local"
    session = "session"


#: Sane defaults for the two history dials, shared by the ORM, the API schemas and the
#: migration that introduced them, so "what is the default" has exactly one answer.
#:
#: 20 sending turns replaces the old `everything`, which was never a sane default: the
#: OpenAI protocol is stateless, so the whole transcript went up on every single turn.
#: Against a gateway that owns the session that is waste; against a provider reached on
#: the user's own key it is a bill, charged again every turn. 20 is enough
#: conversation to feel continuous and small enough that nobody discovers it by invoice.
#:
#: 200 held turns is a guess at "generous, but not a browser eating a gigabyte". Nobody
#: has measured it. It is a default, not a law — the dial is right there.
DEFAULT_SEND_TURNS = 20
DEFAULT_WINDOW_TURNS = 200

#: An upper bound so a typo cannot ask a browser to hold a million turns. High enough
#: that nobody legitimate will ever meet it.
MAX_TURNS = 10_000


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))

    #: argon2id. Never logged, never returned by any serializer.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=16), nullable=False, default=Role.user
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Bumped to invalidate every outstanding access token for this user at once.
    #: The mechanism behind "log out everywhere", role changes and deactivation —
    #: access tokens carry it as the `tv` claim and are rejected when it no longer
    #: matches. This is what buys revocation-within-one-request instead of
    #: revocation-within-15-minutes.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Set on bootstrap and on admin-reset. Blocks every authenticated route except
    #: password-change and logout.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    #: Push to talk. One speech-to-text provider for every pick, not one
    #: per pick: it is the user's voice being heard, and that does not change with who
    #: is listening. Null is "no push to talk", and the 🎤 petal does not appear.
    #: ON DELETE SET NULL, so deleting the provider turns push to talk off.
    stt_provider_id: Mapped[str | None] = mapped_column(
        String(36),
        # use_alter: provider_secrets already points at users (whose provider it is), so
        # this closes a cycle that create_all could not otherwise order. SQLite, which
        # has no ALTER for constraints, renders it inline anyway.
        ForeignKey("provider_secrets.id", ondelete="SET NULL",
                   use_alter=True, name="fk_users_stt_provider"),
    )
    #: The model name sent to the provider. Some servers ignore it; OpenAI's does not.
    stt_model: Mapped[str | None] = mapped_column(String(256))
    #: A language hint such as "en". Null is "let the provider detect it".
    stt_language: Mapped[str | None] = mapped_column(String(16))
    #: Fire and forget: send the transcript the moment the user lets go, rather than
    #: putting it in the text box to be read first. Off unless asked for.
    stt_send_on_release: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Tell the weft a message was spoken, so it can forgive what transcription got
    #: wrong. On unless turned off.
    stt_tell_weft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Tips show by themselves. Kept with the account, not the device, so
    #: turning them off on a phone turns them off everywhere. Hidden tips ignore it.
    tips_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="RefreshToken.user_id"
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    providers: Mapped[list["ProviderSecret"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        # Two foreign keys join these tables now: whose provider it is, and which one
        # hears this user's voice (stt_provider_id). This collection is the first.
        foreign_keys="ProviderSecret.user_id",
    )

    wefts: Mapped[list["Weft"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True,
    )
    tip_marks: Mapped[list["TipMark"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True,
    )

    def __repr__(self) -> str:
        # Deliberately excludes password_hash. A model that prints its own secret ends
        # up in a log line eventually.
        return f"<User {self.username} role={self.role.value} active={self.is_active}>"


class RefreshToken(Base):
    """One row per issued refresh token. Rotation makes each *family* a linked list:
    every refresh marks the presented row used and points ``replaced_by`` at its
    successor.

    Keeping used rows rather than deleting them is the entire basis of reuse detection
    — a token presented twice is only detectable if the first use left a trace.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: The session/device. Survives rotation; matches the `sid` claim on access tokens
    #: and the id of its ``AuthSession`` row. Not a foreign key: adding one would mean
    #: rebuilding this table on sqlite for a link the code already keeps.
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    #: SHA-256 of the token. Fast on purpose: this is a 256-bit random value, not a
    #: password, so there is nothing for a slow hash to defend against and argon2 here
    #: would just make every refresh expensive.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    #: Absolute family deadline, copied onto every member. Independent of rotation, so
    #: an attacker who keeps rotating still loses the session on schedule.
    family_expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replaced_by: Mapped[str | None] = mapped_column(String(36))

    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_reason: Mapped[str | None] = mapped_column(String(64))

    #: Context for the user's own device list, and for the log line when reuse fires.
    user_agent: Mapped[str | None] = mapped_column(String(256))
    ip: Mapped[str | None] = mapped_column(String(64))

    user: Mapped["User"] = relationship(back_populates="refresh_tokens", foreign_keys=[user_id])

    __table_args__ = (
        # Reuse detection and family revocation both scan by family; logout-all scans
        # by user. Both are on the hot auth path.
        Index("ix_refresh_family_revoked", "family_id", "revoked_at"),
    )


class AuthSession(Base):
    """One signed-in device: a refresh-token family, as a row of its own.

    The token rows say *how* a device stays signed in; this says *that* it is, which is
    what the devices list reads and what every authenticated request
    checks. Without it, revoking one device left its access token working for up to
    the access-token TTL, because the only per-request check was ``token_version`` —
    and that is per account, so it can only sign out every device at once.

    Kept in step by ``app/refresh.py`` and nowhere else: a family starts, rotates and is
    revoked there, and its row here moves with it in the same transaction.
    """

    __tablename__ = "auth_sessions"

    #: The family id. Also the access token's `sid` claim.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: Moved on every refresh, not every request: about once every twelve minutes while
    #: a device has the app open. A write on every API call to sharpen it would cost
    #: more than "active now" is worth.
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: The family's absolute deadline, the same value its token rows carry.
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    #: Where it was last seen from. Raw, as the token rows keep them; the list parses
    #: the user agent on read (``app/useragent.py``).
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))

    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_reason: Mapped[str | None] = mapped_column(String(64))


class Invite(Base):
    """A single-use, time-boxed grant to create one account at a given role.

    The plaintext token is shown exactly once, in the response that creates it, and
    only its digest is stored — so a database read does not yield usable invites.
    """

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    #: The role this invite grants. Only an owner may mint one for `admin`.
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=16), nullable=False, default=Role.user
    )

    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: SET NULL, not CASCADE and not the default RESTRICT. Default behaviour would
    #: block deleting any user who had ever redeemed an invite — a foreign key
    #: quietly making accounts undeletable. CASCADE would be worse: it would erase the
    #: audit record of the invite. Nulling keeps "this invite was redeemed, by someone
    #: since removed", which is the honest state.
    redeemed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )

    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ProviderSecret(Base):
    """A user's own upstream endpoint and its bearer token, encrypted at rest.

    This is the load-bearing table of the product: a weft user brings their own
    OpenAI-compatible providers, and model discovery is per-user against these rows.
    The bridge, when it arrives, is just another row here holding that user's own
    bridge token.

    The browser never sees ``secret_ciphertext`` decrypted. There is no endpoint that
    returns the plaintext — not for the owner, not for an admin. Reads return ``hint``.
    """

    __tablename__ = "provider_secrets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: The friendly name from the providers menu, e.g. "OpenRouter". Combined with the
    #: model id this is what gives `OpenRouter/Kai` its collision-free namespacing.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[ProviderKind] = mapped_column(
        Enum(ProviderKind, native_enum=False, length=16), nullable=False, default=ProviderKind.llm
    )
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)

    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: Fresh 12 random bytes per write. Never reused under one key — with GCM that is
    #: a key-compromising failure, not a degradation.
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    #: Which vault key sealed this row, so a rotation can decrypt old rows.
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Last 4 characters of the plaintext, for masked read-back ("••••7f3a"). Four
    #: characters of a provider key is not enough to use and is enough to recognise.
    hint: Mapped[str | None] = mapped_column(String(8))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="providers", foreign_keys=[user_id])
    #: The wefts this provider answers, for counting. View-only, so the ORM never acts
    #: on them: deleting the provider unbinds them through the database's ON DELETE SET
    #: NULL, and there is deliberately no delete cascade.
    wefts: Mapped[list["Weft"]] = relationship(foreign_keys="Weft.provider_id", viewonly=True)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_provider_user_name"),
    )


class Weft(Base):
    """One weft: a name the user chose, and everything that makes it look and sound
    like itself.

    **A weft is created by name and owns its configuration**. The chat
    window's Weft menu lists wefts, and every setting on the weft's page (colours,
    picture, voice, history, headers) is a column here.

    **The engine is a binding, not a parent.** ``provider_id`` and ``model_id`` say what
    answers this weft right now, and both may be null: a weft added a moment ago has no
    engine yet, and a weft whose provider was deleted is left standing with none. This
    table used to be ``selected_models``, a row per (provider, model) the user ticked,
    and its config died with the tick or the provider. Turning the
    binding into two nullable columns is what makes that impossible rather than fixed:
    nothing a provider screen does can reach these rows except to unbind them.

    **Two wefts may share an engine.** The same model with different headers, sessions
    or voices is two different wefts, so there is no unique constraint on the pair.

    **``model_id`` is not validated against the upstream list.** Doing so would mean an
    outbound HTTP call on every save, to re-check ids the client read from us seconds
    earlier. A model that vanishes upstream simply fails when it is used.
    """

    __tablename__ = "wefts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: What the user called it: "Jarvis". Shown as typed, trimmed.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The name folded for comparison (see ``weft_name_key``), so "jarvis" and "Jarvis "
    #: are the same weft as far as "you already have one" is concerned.
    name_key: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The chat provider that answers this weft, or null for none. SET NULL, never
    #: CASCADE: deleting a provider unbinds its wefts and leaves them otherwise whole.
    provider_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("provider_secrets.id", ondelete="SET NULL"), index=True
    )

    #: The upstream model identifier, verbatim: "moonshotai/kimi-k2.6", "bridge/juno".
    #: Long because some gateways namespace generously. Null when unbound.
    model_id: Mapped[str | None] = mapped_column(String(256))

    #: The session id this weft keeps its thread under when nobody typed one, fixed when
    #: the weft is made (see app/sessions.py). Stored rather than derived on every read,
    #: so it never moves: not when the weft is renamed, and not when it is bound to a
    #: different engine. A weft on a new engine is still the same weft, with the same
    #: thread.
    auto_session_id: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    # ---- voice ----
    #
    # A weft can be given a voice, and then replies down that thread are spoken. All
    # three columns are nullable and move together: null means silent, which is the
    # default and the correct behaviour for a window that should not start making noise
    # on its own.
    #
    # The voice belongs to the WEFT, not to the provider and not to the account. Iris
    # sounds like Iris whichever gateway she is reached through, and a plain model on
    # OpenRouter can be given a different voice again — which is the whole point of
    # hanging it here rather than on a global setting.

    #: The TTS provider (a ProviderSecret with kind=tts) that speaks this thread.
    #:
    #: SET NULL rather than CASCADE. Deleting a speech provider should make its threads
    #: fall silent, not delete the wefts that used it.
    tts_provider_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("provider_secrets.id", ondelete="SET NULL")
    )

    #: The speech model, e.g. "kokoro" or "tts-1". Separate from the voice because the
    #: OpenAI speech protocol takes both, and the same voice name can exist under more
    #: than one model.
    tts_model: Mapped[str | None] = mapped_column(String(128))

    #: The voice, e.g. "af_bella".
    tts_voice: Mapped[str | None] = mapped_column(String(128))

    # ---- history (2026-09-16) ----
    #
    # Both hang off the WEFT for the same reason the voice does: bridge/iris and
    # bridge/juno are two beings on one endpoint, and google/gemini and
    # openrouter/grok are two different relationships. A global setting would be wrong
    # for one of them the moment a user has two.
    #
    # Defaults are the behaviour that existed before this column did, so a migration
    # changes nothing for anyone until they ask it to.

    #: Whether this thread is kept on the device that shows it. The service stores no
    #: conversation either way — see PickHistory.
    history: Mapped[PickHistory] = mapped_column(
        Enum(PickHistory, native_enum=False, length=16),
        nullable=False,
        default=PickHistory.memory,
        server_default=PickHistory.memory.value,
    )

    # ---- the dials (2026-09-23) ----
    #
    # Integers rather than the enum that used to live here, and that is the whole point
    # rather than a storage detail. The panel shows a friendly sentence ("the last 20
    # turns", "this turn only") and the Advanced section shows a number, and they are
    # the SAME VALUE seen twice. An enum beside a number would be two fields that can
    # disagree, and then something has to arbitrate — which is how a setting ends up
    # quietly ignoring what the user just typed.

    #: How many turns of the thread go upstream with each message.
    #:
    #: A turn is a user message and its reply. 1 sends the current turn alone, for a
    #: gateway that owns the session and discards whatever history it is handed — the
    #: bridge and hermes both do. 0 means "no cap beyond what the window holds", which
    #: is what the old `everything` became: once the window is bounded, `everything` is
    #: a promise nothing can keep, because turns that have fallen out of the window
    #: cannot be sent by anybody.
    send_turns: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_SEND_TURNS,
        server_default=str(DEFAULT_SEND_TURNS),
    )

    #: How many turns the window holds on the device, in BOTH storage modes — the
    #: in-memory array and the localStorage souvenir obey the same number. Older turns
    #: fall off the top and are gone; this is the only cap that makes the browser's
    #: appetite finite, and the reason the quota guard has something bounded to write.
    window_turns: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_WINDOW_TURNS,
        server_default=str(DEFAULT_WINDOW_TURNS),
    )

    # ---- colours (2026-10-03) ----
    #
    # The pair a weft wears in the chat window: A on the assistant's bubbles, the donut's
    # rings and the pea in the Weft menu; B on the user's bubbles and the far end of every
    # gradient. Per weft for the same reason as everything above — two models behind one
    # endpoint are two different conversations, and should not have to look alike.
    #
    # Both or neither. Null means the theme's own pair, which is what every weft wore
    # before these columns existed, so the migration changes nothing on screen.
    #
    # Always "#rrggbb", lowercase. The client feeds these straight into CSS custom
    # properties and into the donut's colour mixing, which parses exactly six hex digits;
    # the route refuses anything else, so neither ever has to defend itself.

    #: The weft's own colour.
    colour_a: Mapped[str | None] = mapped_column(String(7))

    #: Its partner: the user's side of the conversation, and the gradient's far end.
    colour_b: Mapped[str | None] = mapped_column(String(7))

    # ---- session store (2026-10-04) ----
    #
    # Where the thread is kept when `history` is `session`. Per weft, like everything
    # above: Iris's thread and the plain model's thread are different conversations and
    # may live in different stores, or one in a store and one nowhere.

    #: The session provider (a ProviderSecret with kind=session) that holds this thread.
    #: SET NULL for the voice's reason: deleting a store should leave the weft standing,
    #: just with nowhere to keep its thread. A weft set to `session` with no provider
    #: keeps nothing, which is the safe failure.
    session_provider_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("provider_secrets.id", ondelete="SET NULL")
    )

    #: A session id the user typed, to join an existing session (one a harness already
    #: writes, say). Null means ``auto_session_id``, which is the normal case and needs
    #: no typing.
    session_id: Mapped[str | None] = mapped_column(String(128))

    # ---- background (2026-10-04) ----
    #
    # The picture the chat window paints behind the donut while this weft is current.
    # Per weft like the colours: two wefts behind one endpoint get two pictures.
    #
    # Furniture, not conversation, so it may live here. The bytes do not: they sit
    # in data/backgrounds/<this>.webp, named after their own SHA-256 once the upload has
    # been re-encoded (app/backgrounds.py). Content-addressed, so the same picture on two
    # wefts is one file, and a URL built from it can be cached forever.

    #: Hex SHA-256 of the stored WebP, or null for no picture.
    background: Mapped[str | None] = mapped_column(String(64))

    # ---- request headers (2026-10-06) ----
    #
    # Extra HTTP headers sent with this weft's chat requests, for a gateway that reads
    # something from them: a harness that pins its own session from a header, say. A
    # bridge in front of such a harness sets them in its own config; a weft that talks
    # to the harness directly has nowhere else to say them. Per weft for the usual
    # reason: two agents behind one endpoint can want two different sessions.
    #
    # A list of ``[name, value]`` pairs rather than an object, so the order the user
    # typed them is the order the panel shows them back. Null is "none", which is what
    # every weft had before this column. Never a secret: the values are shown back in the
    # clear, and the names that carry credentials or framing are refused by the route
    # (``upstream.clean_headers``), so nothing here can override what Weft itself sends.

    #: ``[[name, value], ...]``, or null for none.
    headers: Mapped[list[list[str]] | None] = mapped_column(JSON)

    provider: Mapped["ProviderSecret | None"] = relationship(foreign_keys=[provider_id])

    #: The speech provider, if one is set. Distinct from `provider` above: this row has
    #: three foreign keys into the same table, which is why every relationship has to
    #: name its own column explicitly.
    tts_provider: Mapped["ProviderSecret | None"] = relationship(
        foreign_keys=[tts_provider_id]
    )

    #: The session store, if one is set.
    session_provider: Mapped["ProviderSecret | None"] = relationship(
        foreign_keys=[session_provider_id]
    )

    #: The MCP door's key, if one has been minted: the door is set up. Read-only from
    #: here; the door routes mint and delete keys themselves.
    door_key: Mapped["WeftKey | None"] = relationship(viewonly=True, uselist=False)

    def bound(self) -> bool:
        """True when something answers this weft: a provider and a model, both."""
        return bool(self.provider_id and self.model_id)

    def speaks(self) -> bool:
        """True when this weft has everything it needs to be spoken.

        All three or none: a voice with no provider has nowhere to go, and a provider
        with no voice has nothing to say.
        """
        return bool(self.tts_provider_id and self.tts_model and self.tts_voice)

    __table_args__ = (
        # The constraint is the real guard against two wefts with one name, not a
        # pre-check: two tabs adding "Jarvis" at once would both pass a pre-check.
        UniqueConstraint("user_id", "name_key", name="uq_weft_user_name"),
    )


class WeftKey(Base):
    """The key a weft's agent holds to Weft's MCP door.

    One per weft at most: minting a new one replaces the old, and deleting it closes
    the door. Per weft rather than per account, so each weft's agent is scoped to the
    weft it speaks for and can be cut off alone.

    Only the digest is stored, as for invites and refresh tokens. The key is shown once,
    in the response that mints it.
    """

    __tablename__ = "weft_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: CASCADE: a deleted weft takes its door with it.
    weft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wefts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    #: The key's last four characters, so the weft page can say which key is live.
    hint: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: When an agent last came through the door with it, or null for never.
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Notification(Base):
    """A report a weft's agent sent through the weft's MCP door.

    Waiting until the user dismisses it, and then gone: dismissing deletes the row, so
    there is no read state to keep and nothing to retain. A notification belongs to its
    weft, not to the key that sent it, so a key replaced or withdrawn leaves the
    reports already sent where they are.

    ``user_id`` repeats what the weft already says, so the poll can count a user's
    notifications from this table alone.
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: CASCADE: a deleted weft takes its notifications with it.
    weft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wefts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Plain text, read like a reply: a picture reference from ``upload`` shows as the
    #: picture.
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


def weft_name_key(name: str) -> str:
    """A weft's name as compared: runs of whitespace made one space, case folded."""
    return " ".join(name.split()).casefold()


class TipMark(Base):
    """A tip this user has finished, or a hidden one they have found.

    One row per tip and `since`: a tip whose `since` moves is unfinished again, and the
    row for the old value stays as the record of when it was first done. Rows are only
    ever added or wiped, never changed, so two devices finishing tips at once cannot
    lose each other's work. The tip ids are the files' (``app/tips.py``); a row for a
    tip that no longer ships is harmless and simply never matches.
    """

    __tablename__ = "tip_marks"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    tip_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    since: Mapped[str] = mapped_column(String(32), primary_key=True, default="")
    done_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class LoginAttempt(Base):
    """Append-only audit of authentication attempts, and the backing store for
    per-username lockout.

    Records the username as *submitted*, not a user id, because the attempts worth
    investigating are the ones against accounts that do not exist.
    """

    __tablename__ = "login_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, index=True)

    __table_args__ = (
        Index("ix_login_attempt_username_at", "username", "at"),
        Index("ix_login_attempt_ip_at", "ip", "at"),
    )
