"""
Login rate limiting: per-username lockout and per-IP sliding window.

Backed by the ``login_attempts`` table rather than an in-process dict. That costs one
insert per login attempt and buys two things worth more than the write: the limits
survive a restart (otherwise `docker compose restart` is a lockout reset anyone can
trigger by making the service unhealthy), and the table doubles as the audit trail it
was designed to be — "who tried to log in as the owner at 3am" is a question you only
get to ask if the rows exist.

**Two limits, because they stop different attacks.**

*Per-username* stops credential stuffing against one account from anywhere: an attacker
with a botnet rotating IPs still only gets N guesses at ``admin``.

*Per-IP* stops one host enumerating many accounts: an attacker who has a password dump
and is trying it against every username is not caught by the per-username limit at all,
because each account only sees one attempt.

**Lockout is not account disablement.** It expires on its own and locks nobody out
permanently, precisely so that an attacker cannot deny service to a real user by
deliberately failing their login — which is the trap with "disable after N failures".
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import LoginAttempt, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LimitDecision:
    """The outcome of a rate-limit check."""

    allowed: bool
    retry_after_seconds: int = 0
    reason: str = ""


async def record_attempt(
    session: AsyncSession, username: str, ip: str | None, *, succeeded: bool
) -> None:
    """Append one attempt. Called for every login, successful or not.

    Successful attempts are recorded too: they are what ``check`` uses to decide a
    lockout has been cleared, and they are half of the audit trail's value.
    """
    session.add(
        LoginAttempt(
            # Truncate rather than reject — username is attacker-controlled and must
            # not be able to fail the insert and thereby skip its own rate limiting.
            username=(username or "")[:64],
            ip=(ip or "")[:64] or None,
            succeeded=succeeded,
        )
    )
    await session.flush()


async def check(session: AsyncSession, username: str, ip: str | None) -> LimitDecision:
    """Decide whether this login attempt may proceed.

    Called *before* the password is verified, so a locked-out attacker does not even
    get the timing signal from a real argon2 verify.
    """
    max_attempts = int(config.get("security.login_max_attempts") or 5)
    lockout_seconds = int(config.get("security.login_lockout_seconds") or 900)
    ip_window = int(config.get("security.login_ip_window_seconds") or 300)
    ip_max = int(config.get("security.login_ip_max") or 20)

    now = utcnow()

    # ---- per-username lockout ----
    # Count consecutive failures since the last SUCCESS, not failures in a window.
    # Window-counting lets an attacker pace their guesses to sit just under the limit
    # forever; "since last success" means the counter only clears when someone actually
    # authenticates.
    last_success = (
        await session.execute(
            select(func.max(LoginAttempt.at)).where(
                LoginAttempt.username == username[:64],
                LoginAttempt.succeeded.is_(True),
            )
        )
    ).scalar()

    failure_query = select(
        func.count(LoginAttempt.id), func.max(LoginAttempt.at)
    ).where(
        LoginAttempt.username == username[:64],
        LoginAttempt.succeeded.is_(False),
    )
    if last_success is not None:
        failure_query = failure_query.where(LoginAttempt.at > last_success)

    failures, last_failure = (await session.execute(failure_query)).one()
    failures = failures or 0

    if failures >= max_attempts and last_failure is not None:
        unlock_at = last_failure + timedelta(seconds=lockout_seconds)
        if unlock_at > now:
            return LimitDecision(
                allowed=False,
                retry_after_seconds=max(1, int((unlock_at - now).total_seconds())),
                reason="username_locked",
            )
        # The lockout has aged out. Attempts are allowed again without clearing the
        # rows — the audit trail is append-only, and a fresh failure simply restarts
        # the clock from its own timestamp.

    # ---- per-IP sliding window ----
    if ip:
        since = now - timedelta(seconds=ip_window)
        ip_failures = (
            await session.execute(
                select(func.count(LoginAttempt.id)).where(
                    LoginAttempt.ip == ip[:64],
                    LoginAttempt.succeeded.is_(False),
                    LoginAttempt.at > since,
                )
            )
        ).scalar() or 0

        if ip_failures >= ip_max:
            return LimitDecision(
                allowed=False,
                retry_after_seconds=ip_window,
                reason="ip_throttled",
            )

    return LimitDecision(allowed=True)


async def prune(session: AsyncSession, *, older_than_days: int = 90) -> int:
    """Delete attempts older than the audit horizon.

    Run by ``app/housekeeping.py``. The table grows by one row per login attempt, which
    on a busy instance under attack is the one table that can grow quickly.
    """
    from sqlalchemy import delete

    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await session.execute(delete(LoginAttempt).where(LoginAttempt.at < cutoff))
    return result.rowcount or 0
