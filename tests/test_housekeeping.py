"""
Tests for app/housekeeping.py — the sweep that finally calls the two prune functions.

What matters is that it is wired to both, commits, and never takes the service down with
it: it runs as a background task for the life of the process, and an exception escaping
it would end the sweeping for good without anyone noticing.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db, housekeeping, refresh
from app.models import AuthSession, LoginAttempt, RefreshToken, User, utcnow


@pytest.fixture
def swept_database(session, monkeypatch):
    """Point the sweep at the test database rather than the service's own."""
    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    monkeypatch.setattr(db, "get_sessionmaker", lambda: maker)
    return maker


async def count(maker, model) -> int:
    async with maker() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.asyncio
async def test_a_sweep_removes_ended_devices_and_old_attempts(session, swept_database, app_config):
    user = User(username="alice", password_hash="$argon2id$fake")
    session.add(user)
    await session.commit()
    _, live = await refresh.start_family(session, user.id)
    _, ended = await refresh.start_family(session, user.id)
    await refresh.revoke_family(session, ended, reason="logout")
    await session.commit()
    (await session.get(AuthSession, ended)).revoked_at = utcnow() - timedelta(days=30)
    for row in (await session.execute(
        select(RefreshToken).where(RefreshToken.family_id == ended)
    )).scalars():
        row.revoked_at = utcnow() - timedelta(days=30)
    session.add(LoginAttempt(
        username="alice", ip="203.0.113.7", succeeded=False, at=utcnow() - timedelta(days=200)
    ))
    await session.commit()

    await housekeeping.sweep()

    assert await count(swept_database, AuthSession) == 1
    assert await count(swept_database, RefreshToken) == 1
    assert await count(swept_database, LoginAttempt) == 0


@pytest.mark.asyncio
async def test_a_failing_sweep_logs_and_returns(monkeypatch, caplog):
    def broken():
        raise RuntimeError("database gone")

    monkeypatch.setattr(db, "get_sessionmaker", broken)
    await housekeeping.sweep()
    assert "housekeeping failed" in caplog.text
