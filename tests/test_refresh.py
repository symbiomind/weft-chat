"""
Tests for app/refresh.py.

**Why this file matters.** Rotation with reuse detection is the difference between "an
attacker who steals a refresh cookie has a 30-day credential" and "an attacker who
steals a refresh cookie gets caught the next time either party refreshes". The
detection only works if three things hold, and each has a test here:

1. Spent rows are **kept**, not deleted — otherwise a replay is indistinguishable from
   a typo (`test_used_rows_are_retained_not_deleted`).
2. A replay revokes **the whole family** — revoking just the presented row would leave
   the attacker's successor alive (`test_reuse_revokes_the_entire_family`).
3. It revokes **only** that family — a stolen laptop cookie must not log you out of
   your phone (`test_reuse_does_not_touch_other_families`).

The absolute family deadline gets its own test because it is the one bound an attacker
cannot extend by rotating.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config, refresh
from app.models import AuthSession, RefreshToken, User, utcnow


@pytest.fixture(autouse=True)
def configured(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "server:\n"
        "  security:\n"
        "    refresh_token_ttl_days: 30\n"
        "    session_absolute_ttl_days: 30\n",
        encoding="utf-8",
    )
    config.load_config(str(path), force=True)


async def make_user(session, username="alice") -> User:
    user = User(username=username, password_hash="$argon2id$fake")
    session.add(user)
    await session.commit()
    return user


async def rows_for(session, family_id: str) -> list[RefreshToken]:
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.family_id == family_id)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plaintext_token_is_never_stored(session):
    """A database read must not yield a usable token."""
    user = await make_user(session)
    raw, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    stored = (await rows_for(session, family_id))[0]
    assert stored.token_hash != raw
    assert len(stored.token_hash) == 64  # sha256 hex
    assert raw not in stored.token_hash


@pytest.mark.asyncio
async def test_issued_tokens_are_unique_and_high_entropy(session):
    user = await make_user(session)
    tokens = set()
    for _ in range(20):
        raw, _ = await refresh.start_family(session, user.id)
        tokens.add(raw)
    await session.commit()

    assert len(tokens) == 20
    assert all(len(t) >= 40 for t in tokens)


@pytest.mark.asyncio
async def test_user_agent_and_ip_are_truncated_not_rejected(session):
    """Both are attacker-controlled strings. They must not be able to overflow a column
    and fail the login with a database error."""
    user = await make_user(session)
    await refresh.start_family(session, user.id, user_agent="U" * 5000, ip="I" * 500)
    await session.commit()

    row = (await session.execute(select(RefreshToken))).scalars().one()
    assert len(row.user_agent) == 256
    assert len(row.ip) == 64


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotation_issues_a_new_token_in_the_same_family(session):
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    second, consumed = await refresh.rotate(session, first)
    await session.commit()

    assert second != first
    assert consumed.family_id == family_id
    assert consumed.user_id == user.id
    assert len(await rows_for(session, family_id)) == 2


@pytest.mark.asyncio
async def test_used_rows_are_retained_not_deleted(session):
    """Reuse is only detectable if the first use left a trace."""
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    await refresh.rotate(session, first)
    await session.commit()

    rows = await rows_for(session, family_id)
    spent = [r for r in rows if r.used_at is not None]
    assert len(spent) == 1
    assert spent[0].replaced_by is not None, "the family must be a linked list"


@pytest.mark.asyncio
async def test_the_successor_can_itself_be_rotated(session):
    user = await make_user(session)
    token, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    for _ in range(5):
        token, _ = await refresh.rotate(session, token)
        await session.commit()

    assert len(await rows_for(session, family_id)) == 6


# ---------------------------------------------------------------------------
# Reuse detection — the point of the whole design
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replaying_a_spent_token_raises_reuse(session):
    user = await make_user(session)
    first, _ = await refresh.start_family(session, user.id)
    await session.commit()
    await refresh.rotate(session, first)
    await session.commit()

    with pytest.raises(refresh.RefreshReuseError):
        await refresh.rotate(session, first)


@pytest.mark.asyncio
async def test_reuse_revokes_the_entire_family(session):
    """Including the successor the legitimate client is holding. Both parties are
    logged out of that device, because we cannot tell which one is the thief."""
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    second, _ = await refresh.rotate(session, first)
    await session.commit()

    with pytest.raises(refresh.RefreshReuseError):
        await refresh.rotate(session, first)

    rows = await rows_for(session, family_id)
    assert all(r.revoked_at is not None for r in rows)
    assert all(r.revoked_reason == "reuse_detected" for r in rows)

    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, second)


@pytest.mark.asyncio
async def test_reuse_does_not_touch_other_families(session):
    """The fail-safe boundary: a stolen laptop cookie must not log you out of your
    phone. Revoking every session on any reuse would be over-reaction."""
    user = await make_user(session)
    laptop, laptop_family = await refresh.start_family(session, user.id)
    phone, phone_family = await refresh.start_family(session, user.id)
    await session.commit()

    await refresh.rotate(session, laptop)
    await session.commit()
    with pytest.raises(refresh.RefreshReuseError):
        await refresh.rotate(session, laptop)

    assert all(r.revoked_at is not None for r in await rows_for(session, laptop_family))
    assert all(r.revoked_at is None for r in await rows_for(session, phone_family))

    new_phone, _ = await refresh.rotate(session, phone)
    assert new_phone, "the untouched device must still work"


@pytest.mark.asyncio
async def test_reuse_of_an_expired_spent_token_still_counts_as_reuse(session):
    """Order matters in rotate(): reuse is checked before expiry. A replayed token that
    has also expired is still the signal that a family was compromised."""
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    await refresh.rotate(session, first)
    await session.commit()

    for row in await rows_for(session, family_id):
        row.expires_at = utcnow() - timedelta(days=1)
    await session.commit()

    with pytest.raises(refresh.RefreshReuseError):
        await refresh.rotate(session, first)


# ---------------------------------------------------------------------------
# Rejection cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_token_is_rejected(session):
    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, "a-token-that-was-never-issued")


@pytest.mark.asyncio
async def test_empty_token_is_rejected(session):
    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, "")


@pytest.mark.asyncio
async def test_expired_token_is_rejected(session):
    user = await make_user(session)
    raw, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    row = (await rows_for(session, family_id))[0]
    row.expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()

    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, raw)


@pytest.mark.asyncio
async def test_revoked_token_is_rejected(session):
    user = await make_user(session)
    raw, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    await refresh.revoke_family(session, family_id, reason="logout")
    await session.commit()

    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, raw)


@pytest.mark.asyncio
async def test_family_absolute_deadline_cannot_be_extended_by_rotating(session):
    """The one bound an attacker holding a live token cannot move. Rotation extends the
    individual token; it never extends the family."""
    user = await make_user(session)
    raw, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    for row in await rows_for(session, family_id):
        row.family_expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()

    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, raw)

    rows = await rows_for(session, family_id)
    assert all(r.revoked_reason == "family_expired" for r in rows)


@pytest.mark.asyncio
async def test_successor_inherits_the_family_deadline(session):
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    original = (await rows_for(session, family_id))[0].family_expires_at

    await refresh.rotate(session, first)
    await session.commit()

    assert all(r.family_expires_at == original for r in await rows_for(session, family_id))


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_one_kills_the_family_not_just_the_row(session):
    """Logout ends the session on that device; the presented row is only its current
    link."""
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    second, _ = await refresh.rotate(session, first)
    await session.commit()

    assert await refresh.revoke_one(session, second, reason="logout") is True
    await session.commit()

    assert all(r.revoked_at is not None for r in await rows_for(session, family_id))


@pytest.mark.asyncio
async def test_revoke_unknown_token_reports_false_without_raising(session):
    """Logging out with a stale cookie must succeed quietly. Reporting "no such token"
    would confirm to a caller which tokens are real."""
    assert await refresh.revoke_one(session, "never-issued", reason="logout") is False


@pytest.mark.asyncio
async def test_revoke_all_for_user_covers_every_family(session):
    user = await make_user(session)
    families = [(await refresh.start_family(session, user.id))[1] for _ in range(3)]
    await session.commit()

    count = await refresh.revoke_all_for_user(session, user.id, reason="logout_all")
    await session.commit()

    assert count == 3
    for family_id in families:
        assert all(r.revoked_at is not None for r in await rows_for(session, family_id))


@pytest.mark.asyncio
async def test_revoke_all_does_not_touch_another_user(session):
    alice = await make_user(session, "alice")
    bob = await make_user(session, "bob")
    _, bob_family = await refresh.start_family(session, bob.id)
    await refresh.start_family(session, alice.id)
    await session.commit()

    await refresh.revoke_all_for_user(session, alice.id, reason="logout_all")
    await session.commit()

    assert all(r.revoked_at is None for r in await rows_for(session, bob_family))


@pytest.mark.asyncio
async def test_revocation_is_idempotent_and_preserves_the_first_reason(session):
    """The audit trail should say why a session first died, not why it was last
    touched."""
    user = await make_user(session)
    _, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    await refresh.revoke_family(session, family_id, reason="reuse_detected")
    await session.commit()
    second_pass = await refresh.revoke_family(session, family_id, reason="logout")
    await session.commit()

    assert second_pass == 0
    assert all(r.revoked_reason == "reuse_detected" for r in await rows_for(session, family_id))


# ---------------------------------------------------------------------------
# The device row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_starting_a_family_records_the_device(session):
    user = await make_user(session)
    _, family_id = await refresh.start_family(
        session, user.id, user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0", ip="203.0.113.7"
    )
    await session.commit()

    device = await session.get(AuthSession, family_id)
    assert device.user_id == user.id
    assert device.ip == "203.0.113.7"
    assert device.revoked_at is None
    assert device.expires_at == (await rows_for(session, family_id))[0].family_expires_at


@pytest.mark.asyncio
async def test_rotating_moves_last_seen_and_where_from(session):
    user = await make_user(session)
    raw, family_id = await refresh.start_family(session, user.id, ip="203.0.113.7")
    await session.commit()
    device = await session.get(AuthSession, family_id)
    device.last_seen_at = utcnow() - timedelta(hours=3)
    await session.commit()

    await refresh.rotate(session, raw, ip="198.51.100.9", user_agent="new phone")
    await session.commit()

    await session.refresh(device)
    assert (utcnow() - device.last_seen_at).total_seconds() < 60
    assert device.ip == "198.51.100.9"
    assert device.user_agent == "new phone"


@pytest.mark.asyncio
async def test_a_family_whose_device_was_signed_out_cannot_refresh(session):
    user = await make_user(session)
    raw, family_id = await refresh.start_family(session, user.id)
    await session.commit()

    assert await refresh.revoke_session(session, user.id, family_id, reason="test")
    await session.commit()

    with pytest.raises(refresh.RefreshError):
        await refresh.rotate(session, raw)


@pytest.mark.asyncio
async def test_reuse_marks_the_device_signed_out_too(session):
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    await refresh.rotate(session, first)
    await session.commit()

    with pytest.raises(refresh.RefreshReuseError):
        await refresh.rotate(session, first)

    device = await session.get(AuthSession, family_id)
    await session.refresh(device)
    assert device.revoked_reason == "reuse_detected"


@pytest.mark.asyncio
async def test_revoke_all_signs_out_every_device_of_that_user_only(session):
    alice = await make_user(session)
    bob = await make_user(session, "bob")
    alice_families = [(await refresh.start_family(session, alice.id))[1] for _ in range(2)]
    _, bob_family = await refresh.start_family(session, bob.id)
    await session.commit()

    await refresh.revoke_all_for_user(session, alice.id, reason="logout_all")
    await session.commit()

    assert await refresh.live_sessions(session, alice.id) == []
    assert [d.id for d in await refresh.live_sessions(session, bob.id)] == [bob_family]
    for family_id in alice_families:
        assert (await session.get(AuthSession, family_id)).revoked_reason == "logout_all"


@pytest.mark.asyncio
async def test_live_sessions_leaves_out_ended_ones_and_lists_newest_first(session):
    user = await make_user(session)
    _, older = await refresh.start_family(session, user.id)
    _, newer = await refresh.start_family(session, user.id)
    _, expired = await refresh.start_family(session, user.id)
    _, revoked = await refresh.start_family(session, user.id)
    await session.commit()
    (await session.get(AuthSession, older)).last_seen_at = utcnow() - timedelta(hours=2)
    (await session.get(AuthSession, expired)).expires_at = utcnow() - timedelta(seconds=1)
    await refresh.revoke_family(session, revoked, reason="test")
    await session.commit()

    assert [d.id for d in await refresh.live_sessions(session, user.id)] == [newer, older]


@pytest.mark.asyncio
async def test_revoke_session_will_not_touch_someone_elses_device(session):
    alice = await make_user(session)
    bob = await make_user(session, "bob")
    _, bob_family = await refresh.start_family(session, bob.id)
    await session.commit()

    assert not await refresh.revoke_session(session, alice.id, bob_family, reason="test")
    assert not await refresh.revoke_session(session, alice.id, "no-such-family", reason="test")
    await session.commit()

    assert (await session.get(AuthSession, bob_family)).revoked_at is None


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_keeps_live_families_whole_spent_rows_and_all(session):
    """Spent rows of a family still in use are what catches a replay; they stay."""
    user = await make_user(session)
    first, family_id = await refresh.start_family(session, user.id)
    await session.commit()
    await refresh.rotate(session, first)
    await session.commit()
    for row in await rows_for(session, family_id):
        row.issued_at = utcnow() - timedelta(days=25)
    await session.commit()

    assert await refresh.purge_expired(session, grace_days=7) == (0, 0)
    assert len(await rows_for(session, family_id)) == 2


@pytest.mark.asyncio
async def test_purge_removes_families_ended_longer_ago_than_the_grace(session):
    user = await make_user(session)
    _, live = await refresh.start_family(session, user.id)
    _, signed_out_long_ago = await refresh.start_family(session, user.id)
    _, signed_out_yesterday = await refresh.start_family(session, user.id)
    _, expired_long_ago = await refresh.start_family(session, user.id)
    await session.commit()

    await refresh.revoke_family(session, signed_out_long_ago, reason="logout")
    await refresh.revoke_family(session, signed_out_yesterday, reason="logout")
    await session.commit()
    for row in await rows_for(session, signed_out_long_ago):
        row.revoked_at = utcnow() - timedelta(days=8)
    (await session.get(AuthSession, signed_out_long_ago)).revoked_at = utcnow() - timedelta(days=8)
    for row in await rows_for(session, signed_out_yesterday):
        row.revoked_at = utcnow() - timedelta(days=1)
    for row in await rows_for(session, expired_long_ago):
        row.family_expires_at = utcnow() - timedelta(days=8)
    (await session.get(AuthSession, expired_long_ago)).expires_at = utcnow() - timedelta(days=8)
    await session.commit()

    assert await refresh.purge_expired(session, grace_days=7) == (2, 2)
    await session.commit()

    for kept in (live, signed_out_yesterday):
        assert len(await rows_for(session, kept)) == 1
        assert await session.get(AuthSession, kept) is not None
    for gone in (signed_out_long_ago, expired_long_ago):
        assert await rows_for(session, gone) == []
    session.expunge_all()
    for gone in (signed_out_long_ago, expired_long_ago):
        assert await session.get(AuthSession, gone) is None
