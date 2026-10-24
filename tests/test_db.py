"""
Tests for app/db.py and app/models.py.

**Why this file matters.** Two of these tests defend properties that are invisible
until they are violated, at which point the violation is a data-retention incident
rather than a crash:

1. **Foreign keys are actually enforced.** sqlite ships with FK enforcement OFF and it
   is a *per-connection* setting. If the pragma hook regresses, every ON DELETE CASCADE
   in models.py becomes decorative: deleting a user leaves their encrypted provider
   secrets and refresh tokens in the database, unreachable through any account but very
   much still there. `test_deleting_user_cascades_*` are the canaries.

2. **Timestamps survive the round trip as aware UTC.** sqlite returns naive datetimes.
   Every token-expiry comparison in this service is `expires_at <= utcnow()`, and
   against a naive value that either raises TypeError or silently compares wrong. The
   UTCDateTime decorator is what prevents it; these tests prove it is doing its job on
   the way in *and* the way out.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import _install_sqlite_pragmas, _scrub_url, _sync_url
from app.models import (
    Base,
    Invite,
    LoginAttempt,
    ProviderSecret,
    ProviderKind,
    RefreshToken,
    Role,
    User,
    utcnow,
)


@pytest_asyncio.fixture
async def session(tmp_path):
    """A real on-disk sqlite database per test.

    On-disk rather than :memory: on purpose — the pragmas and the FK behaviour under
    test are exactly the things that differ between an in-memory scratch database and
    the file the service actually runs against.
    """
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(url)
    _install_sqlite_pragmas(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()


async def make_user(session, username="alice", role=Role.user) -> User:
    user = User(username=username, password_hash="$argon2id$fake", role=role)
    session.add(user)
    await session.commit()
    return user


# ---------------------------------------------------------------------------
# Pragmas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_foreign_keys_pragma_is_on(session):
    """If this fails, every cascade test below is meaningless and the schema is lying."""
    result = await session.execute(text("PRAGMA foreign_keys"))
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_wal_journal_mode_is_set(session):
    result = await session.execute(text("PRAGMA journal_mode"))
    assert result.scalar().lower() == "wal"


# ---------------------------------------------------------------------------
# Cascades — the data-retention tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deleting_user_cascades_provider_secrets(session):
    """A deleted user's encrypted provider keys must not outlive the account.

    They are unreachable through the API either way, which is precisely why an orphan
    would go unnoticed: still on disk, still decryptable with the vault key, belonging
    to nobody.
    """
    user = await make_user(session)
    session.add(ProviderSecret(
        user_id=user.id, name="OpenRouter", kind=ProviderKind.llm,
        base_url="https://openrouter.ai/api/v1",
        secret_ciphertext=b"ct", secret_nonce=b"0" * 12, key_id="k1", hint="7f3a",
    ))
    await session.commit()

    await session.delete(user)
    await session.commit()

    remaining = (await session.execute(select(ProviderSecret))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_deleting_user_cascades_refresh_tokens(session):
    user = await make_user(session)
    now = utcnow()
    session.add(RefreshToken(
        user_id=user.id, family_id="fam-1", token_hash="a" * 64,
        expires_at=now + timedelta(days=30), family_expires_at=now + timedelta(days=30),
    ))
    await session.commit()

    await session.delete(user)
    await session.commit()

    assert (await session.execute(select(RefreshToken))).scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_redeemer_nulls_the_invite_rather_than_blocking(session):
    """The FK bug this catches: with the default RESTRICT, a user who had ever redeemed
    an invite could never be deleted — a foreign key quietly making accounts permanent.
    SET NULL keeps the audit row and frees the account."""
    owner = await make_user(session, "owner", Role.owner)
    redeemer = await make_user(session, "bob")

    session.add(Invite(
        token_hash="b" * 64, role=Role.user, created_by=owner.id,
        expires_at=utcnow() + timedelta(days=7),
        redeemed_at=utcnow(), redeemed_by=redeemer.id,
    ))
    await session.commit()

    await session.delete(redeemer)
    await session.commit()

    invite = (await session.execute(select(Invite))).scalars().one()
    assert invite.redeemed_by is None, "invite row must survive with a nulled redeemer"
    assert invite.redeemed_at is not None, "the fact of redemption is still true"


@pytest.mark.asyncio
async def test_deleting_creator_cascades_their_invites(session):
    """An invite minted by a removed admin must not remain redeemable."""
    owner = await make_user(session, "owner", Role.owner)
    session.add(Invite(
        token_hash="c" * 64, role=Role.user, created_by=owner.id,
        expires_at=utcnow() + timedelta(days=7),
    ))
    await session.commit()

    await session.delete(owner)
    await session.commit()

    assert (await session.execute(select(Invite))).scalars().all() == []


# ---------------------------------------------------------------------------
# UTCDateTime — the expiry-correctness tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_datetime_round_trips_as_aware_utc(session):
    user = await make_user(session)
    fetched = (await session.execute(select(User).where(User.id == user.id))).scalars().one()

    assert fetched.created_at.tzinfo is not None, "sqlite returned a naive datetime"
    assert fetched.created_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_expiry_comparison_against_utcnow_does_not_raise(session):
    """The actual operation every auth check performs. A naive value here raises
    TypeError, in the auth path, at runtime."""
    user = await make_user(session)
    now = utcnow()
    session.add(RefreshToken(
        user_id=user.id, family_id="fam", token_hash="d" * 64,
        expires_at=now - timedelta(seconds=1), family_expires_at=now + timedelta(days=30),
    ))
    await session.commit()

    token = (await session.execute(select(RefreshToken))).scalars().one()
    assert token.expires_at <= utcnow(), "expired token must compare as expired"
    assert token.family_expires_at > utcnow()


@pytest.mark.asyncio
async def test_naive_datetime_written_is_read_back_as_utc(session):
    """A naive value reaching the DB is an upstream bug, but it must not become an
    implicit-local-offset value on the way back out."""
    user = await make_user(session)
    naive = datetime(2026, 1, 1, 12, 0, 0)
    user.last_login_at = naive
    await session.commit()
    session.expunge_all()

    fetched = (await session.execute(select(User))).scalars().one()
    assert fetched.last_login_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_non_utc_input_is_normalised_not_truncated(session):
    """+10:30 (a half-hour zone) must be stored as the same instant in UTC, not as the wall
    clock with the offset thrown away — a 10.5 hour error in token expiry."""
    user = await make_user(session)
    half_hour_zone = timezone(timedelta(hours=10, minutes=30))
    user.last_login_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=half_hour_zone)
    await session.commit()
    session.expunge_all()

    fetched = (await session.execute(select(User))).scalars().one()
    assert fetched.last_login_at == datetime(2026, 1, 1, 1, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_username_is_unique(session):
    await make_user(session, "alice")
    session.add(User(username="alice", password_hash="x"))
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_token_hash_is_unique(session):
    """Two rows with the same digest would make reuse detection ambiguous."""
    user = await make_user(session)
    now = utcnow()
    for _ in range(2):
        session.add(RefreshToken(
            user_id=user.id, family_id="fam", token_hash="e" * 64,
            expires_at=now + timedelta(days=1), family_expires_at=now + timedelta(days=30),
        ))
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_provider_name_unique_per_user_not_globally(session):
    """Two users must each be able to have a provider called "OpenRouter"."""
    alice = await make_user(session, "alice")
    bob = await make_user(session, "bob")

    for user in (alice, bob):
        session.add(ProviderSecret(
            user_id=user.id, name="OpenRouter", base_url="https://x/v1",
            secret_ciphertext=b"ct", secret_nonce=b"0" * 12, key_id="k1",
        ))
    await session.commit()

    assert len((await session.execute(select(ProviderSecret))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_same_provider_name_twice_for_one_user_is_rejected(session):
    alice = await make_user(session, "alice")
    for _ in range(2):
        session.add(ProviderSecret(
            user_id=alice.id, name="OpenRouter", base_url="https://x/v1",
            secret_ciphertext=b"ct", secret_nonce=b"0" * 12, key_id="k1",
        ))
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_user_defaults_are_safe(session):
    """A user created without explicit flags must land on the least-privileged
    settings: role=user, active, token_version 0."""
    user = await make_user(session)
    assert user.role is Role.user
    assert user.is_active is True
    assert user.token_version == 0
    assert user.must_change_password is False


@pytest.mark.asyncio
async def test_ids_are_uuids_not_sequential(session):
    """Sequential ids leak population size and let one id guess its neighbours."""
    a = await make_user(session, "a")
    b = await make_user(session, "b")
    assert len(a.id) == 36 and "-" in a.id
    assert a.id != b.id


@pytest.mark.asyncio
async def test_user_repr_never_contains_the_password_hash(session):
    """A model that prints its own secret ends up in a log line eventually."""
    user = await make_user(session)
    assert "argon2" not in repr(user)
    assert user.password_hash not in repr(user)


@pytest.mark.asyncio
async def test_login_attempt_records_the_submitted_username(session):
    """Attempts against non-existent accounts are the ones worth investigating, so the
    table must not require a real user id."""
    session.add(LoginAttempt(username="does-not-exist", ip="203.0.113.9", succeeded=False))
    await session.commit()
    row = (await session.execute(select(LoginAttempt))).scalars().one()
    assert row.username == "does-not-exist"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def test_sync_url_strips_async_drivers():
    assert _sync_url("sqlite+aiosqlite:///./data/weft.db") == "sqlite:///./data/weft.db"
    assert _sync_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_scrub_url_hides_credentials():
    """Postgres URLs carry a password and this string is logged at startup."""
    scrubbed = _scrub_url("postgresql+asyncpg://weft:hunter2@db.internal/weft")
    assert "hunter2" not in scrubbed
    assert "db.internal/weft" in scrubbed


def test_scrub_url_leaves_sqlite_alone():
    assert _scrub_url("sqlite+aiosqlite:///./data/weft.db") == "sqlite+aiosqlite:///./data/weft.db"
