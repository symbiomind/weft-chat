"""
Refresh-token families: issue, rotate, detect reuse, revoke.

A *family* is one login on one device. Every refresh consumes the presented token and
issues a successor in the same family, so a family is a linked list of rows —
``replaced_by`` points forward, ``used_at`` marks the ones already spent.

**Why used rows are kept rather than deleted.** Reuse is only detectable if the first
use left a trace. Delete-on-rotate turns a replayed token into "unknown token", which
is indistinguishable from a typo, and the theft goes unnoticed. Keeping the row is the
entire basis of the detection below.

**What reuse detection does when it fires.** The presented token was already spent, so
either an attacker is replaying a stolen cookie or the legitimate client raced itself.
We cannot tell which, so we revoke the whole family: both the thief and the victim are
logged out of that device, and the victim's other devices are untouched. Revoking
*every* session would be over-reaction (a stolen laptop cookie should not log you out
of your phone); revoking only the one row would be under-reaction (the attacker's
successor token would survive).

**The client-side requirement this creates.** There is no grace window, so the web
client MUST funnel every refresh through a single shared in-flight promise::

    let refreshPromise = null;   // every caller awaits the same request

Two tabs refreshing simultaneously would otherwise present the same token twice and
trip the detector. The symptom is random logouts that nobody can reproduce, because
they depend on tab timing. This is a server guarantee that rests on client behaviour,
so it is stated here, in the module the guarantee lives in, rather than only in the JS
where it would read as an optimisation someone could tidy away.

Two smaller obligations in the same family, for whoever writes that client:

* Re-sync the CSRF header after every token-issuing response (login, refresh, password
  change each mint a fresh one and return it in the body). A client that keeps sending
  its previous token gets 403 on its next state-changing request.
* Schedule the refresh from the ``expires_in`` the server returns, at roughly 80% of
  it, and on the first 401. Never compare a server timestamp against the browser clock.

**Every family has an ``AuthSession`` row**, and this module is the only
thing that writes it: started with the family, touched on each rotation, revoked with it.
``app/deps.py`` checks that row on every request, which is what lets one device be
signed out at once rather than when its access token runs out.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import AuthSession, RefreshToken, utcnow

logger = logging.getLogger(__name__)

#: 32 bytes of entropy. token_urlsafe(32) yields a 43-character string.
TOKEN_BYTES = 32


class RefreshError(Exception):
    """Refresh failed. One type, because the caller answers all of them with the same
    401 and a cleared cookie — telling a caller *why* their refresh failed tells an
    attacker whether the token existed."""


class RefreshReuseError(RefreshError):
    """A spent token was presented again. Distinguished from RefreshError only so the
    route can log it loudly and the family can be torn down; the HTTP response is
    identical."""


def hash_token(raw: str) -> str:
    """SHA-256, hex. Fast on purpose.

    This is a 256-bit uniformly random value, not a password: there is no dictionary to
    attack and nothing for a slow hash to defend against. argon2 here would make every
    refresh — which happens every 15 minutes per client — cost 50ms of CPU for no
    security gain.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def issue(
    session: AsyncSession,
    user_id: str,
    *,
    family_id: str,
    family_expires_at: datetime,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    """Create one refresh token row and return the plaintext.

    The plaintext is returned exactly once, to be put straight into a Set-Cookie. Only
    its digest is stored, so a database read yields nothing usable.
    """
    ttl_days = int(config.get("security.refresh_token_ttl_days") or 30)
    raw = generate_token()

    row = RefreshToken(
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_token(raw),
        expires_at=utcnow() + timedelta(days=ttl_days),
        family_expires_at=family_expires_at,
        user_agent=(user_agent or "")[:256] or None,
        ip=(ip or "")[:64] or None,
    )
    session.add(row)
    await session.flush()
    return raw


async def start_family(
    session: AsyncSession,
    user_id: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, str]:
    """Begin a new session (one login on one device). Returns ``(raw_token, family_id)``.

    The absolute family deadline is set here and copied onto every successor, so an
    attacker who keeps rotating still loses the session on schedule. Rotation extends
    the individual token, never the family.
    """
    import uuid

    family_id = str(uuid.uuid4())
    absolute_days = int(config.get("security.session_absolute_ttl_days") or 30)
    now = utcnow()
    family_expires_at = now + timedelta(days=absolute_days)

    session.add(AuthSession(
        id=family_id,
        user_id=user_id,
        created_at=now,
        last_seen_at=now,
        expires_at=family_expires_at,
        ip=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:256] or None,
    ))

    raw = await issue(
        session, user_id,
        family_id=family_id,
        family_expires_at=family_expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    return raw, family_id


async def rotate(
    session: AsyncSession,
    raw_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, RefreshToken]:
    """Consume ``raw_token`` and issue its successor.

    Returns ``(new_raw_token, consumed_row)`` — the caller needs the consumed row's
    ``user_id`` and ``family_id`` to mint the matching access token.

    Raises ``RefreshReuseError`` if the token was already spent (and revokes the
    family before raising), or ``RefreshError`` for every other failure.
    """
    if not raw_token:
        raise RefreshError("no refresh token presented")

    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )
    ).scalars().one_or_none()

    if row is None:
        raise RefreshError("unknown refresh token")

    # Order matters: check reuse BEFORE expiry. A replayed token that has also expired
    # is still a replay, and still the signal that a family should be torn down.
    if row.used_at is not None:
        logger.warning(
            "REFRESH TOKEN REUSE detected for user %s family %s (presented from ip=%s) "
            "— revoking the entire family. Either a token was stolen, or a client "
            "refreshed twice concurrently without a shared in-flight promise.",
            row.user_id, row.family_id, ip,
        )
        await revoke_family(session, row.family_id, reason="reuse_detected")
        await session.commit()
        raise RefreshReuseError("refresh token reuse")

    if row.revoked_at is not None:
        raise RefreshError("refresh token revoked")

    # The device row moves in step with the token rows, so a revoked family is caught
    # above. A family with no row at all predates nothing (the migration that added the
    # table backfilled every family) and is treated as signed out, because that is how
    # app/deps.py treats its access token.
    device = await session.get(AuthSession, row.family_id)
    if device is None or device.revoked_at is not None:
        raise RefreshError("session revoked")

    now = datetime.now(timezone.utc)
    if row.expires_at <= now:
        raise RefreshError("refresh token expired")

    # The absolute deadline. Independent of how often the family has rotated.
    if row.family_expires_at <= now:
        await revoke_family(session, row.family_id, reason="family_expired")
        await session.commit()
        raise RefreshError("session expired")

    successor_raw = await issue(
        session, row.user_id,
        family_id=row.family_id,
        family_expires_at=row.family_expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    successor_hash = hash_token(successor_raw)
    successor = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == successor_hash)
        )
    ).scalars().one()

    row.used_at = now
    row.replaced_by = successor.id
    device.last_seen_at = now
    device.ip = (ip or "")[:64] or device.ip
    device.user_agent = (user_agent or "")[:256] or device.user_agent
    await session.flush()

    return successor_raw, row


async def revoke_family(session: AsyncSession, family_id: str, *, reason: str) -> int:
    """Revoke every live token in one family. Returns the number of rows affected.

    Idempotent, and deliberately does not touch rows that are already revoked so the
    original reason survives for the audit trail.
    """
    now = utcnow()
    await session.execute(
        update(AuthSession)
        .where(AuthSession.id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason[:64])
    )
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason[:64])
    )
    return result.rowcount or 0


async def revoke_all_for_user(session: AsyncSession, user_id: str, *, reason: str) -> int:
    """Revoke every family belonging to a user — "log out everywhere".

    Note this alone is not sufficient to lock someone out: outstanding *access* tokens
    remain valid until they expire. The caller must also bump ``users.token_version``,
    which is what invalidates them immediately. Revoking refresh tokens without bumping
    tv leaves a 15-minute window, and that window is the whole reason `tv` exists.
    """
    now = utcnow()
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason[:64])
    )
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason[:64])
    )
    return result.rowcount or 0


async def revoke_one(session: AsyncSession, raw_token: str, *, reason: str) -> bool:
    """Revoke the family a single presented token belongs to — this is logout.

    Logout revokes the family rather than the row because the point is to end the
    session on that device, and the row is only its current link.
    """
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )
    ).scalars().one_or_none()

    if row is None:
        # Logout of an unknown token is a no-op that still returns success. Reporting
        # "no such token" would confirm to a caller which tokens are real.
        return False

    await revoke_family(session, row.family_id, reason=reason)
    return True


async def live_sessions(session: AsyncSession, user_id: str) -> list[AuthSession]:
    """Every device this user is signed in on: not revoked, not past its deadline.

    Newest activity first. Which one is *this* device is the caller's to say — it is the
    `sid` on the access token, not something stored.
    """
    return list((
        await session.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > utcnow(),
            )
            .order_by(AuthSession.last_seen_at.desc())
        )
    ).scalars())


async def revoke_session(
    session: AsyncSession, user_id: str, family_id: str, *, reason: str
) -> bool:
    """Sign one device out, if it is this user's and still signed in.

    False for a device that is someone else's, already gone, or never was — the caller
    answers all three the same way, so a guessed id tells nobody whether it is real.
    """
    device = await session.get(AuthSession, family_id)
    if device is None or device.user_id != user_id or device.revoked_at is not None:
        return False
    await revoke_family(session, family_id, reason=reason)
    return True


async def purge_expired(session: AsyncSession, *, grace_days: int = 7) -> tuple[int, int]:
    """Delete devices, and their token rows, that ended more than ``grace_days`` ago.

    Returns ``(devices, tokens)`` removed. Run by ``app/housekeeping.py``.

    **Why a dead family's rows can go.** Spent rows are kept so that a replayed token is
    caught (the module docstring). That only matters while the family can still be
    used: once it is revoked or past its deadline, a replay of any of its tokens gets
    the same 401 with or without the row. All the row still buys is the reuse warning
    in the log, and the grace period keeps that for a week after the fact.

    Token rows are matched on their own revoked and deadline columns rather than
    through the device row, so a family that somehow has no device row is still swept.
    """
    cutoff = utcnow() - timedelta(days=grace_days)

    tokens = await session.execute(
        delete(RefreshToken).where(
            or_(RefreshToken.revoked_at < cutoff, RefreshToken.family_expires_at < cutoff)
        )
    )
    devices = await session.execute(
        delete(AuthSession).where(
            or_(AuthSession.revoked_at < cutoff, AuthSession.expires_at < cutoff)
        )
    )
    return devices.rowcount or 0, tokens.rowcount or 0
