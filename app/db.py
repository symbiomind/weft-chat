"""
Database engine, session factory, and migration runner.

**The two sqlite pragmas here are not tuning, they are correctness.**

``foreign_keys=ON`` — sqlite ships with foreign-key enforcement *off* by default, and
it is a per-connection setting. Without it every ``ON DELETE CASCADE`` in models.py is
decorative: deleting a user would leave their refresh tokens and their encrypted
provider secrets behind as orphans, still in the database, no longer reachable through
any account. That is a data-retention bug wearing a schema.

``journal_mode=WAL`` — the default rollback journal takes a write lock that blocks
readers. With a WebSocket sweep, a rate limiter, and request handlers all touching the
same file, the default produces "database is locked" under trivially light load.

Both are applied on every new connection via an event hook, because a pooled connection
that predates the pragma would silently not have it.
"""

import logging
import os
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import config

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

#: Set when `alembic upgrade head` fails. Every non-/health route 503s while true,
#: rather than serving requests against a schema that may be half-built.
_migration_failed: bool = False


def is_sqlite(url: str | None = None) -> bool:
    return (url or config.database_url()).startswith("sqlite")


def get_engine() -> AsyncEngine:
    """The process-wide engine, created on first use."""
    global _engine
    if _engine is not None:
        return _engine

    url = config.database_url()
    kwargs: dict = {"echo": False, "future": True}

    if is_sqlite(url):
        # sqlite's driver default is a 5s busy timeout; WAL plus this makes concurrent
        # writers wait rather than immediately erroring.
        kwargs["connect_args"] = {"timeout": 30}

    _engine = create_async_engine(url, **kwargs)

    if is_sqlite(url):
        _install_sqlite_pragmas(_engine)

    logger.info("Database engine created (%s).", _scrub_url(url))
    return _engine


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Apply the two correctness pragmas to every pooled connection."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # Durable enough for this workload without an fsync per statement.
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # let handlers read attributes after commit
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request, rolled back on any exception.

    The explicit rollback matters for the auth routes: a handler that raises after a
    partial write (say, rotating a refresh token and then failing to mint the access
    token) must not leave the rotation committed.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def migration_failed() -> bool:
    return _migration_failed


def run_migrations() -> bool:
    """Run ``alembic upgrade head``. Returns True on success.

    Runs synchronously, before uvicorn serves traffic: sqlite has no story for
    concurrent DDL, so a migration racing a request handler is not something to find
    out about in production.

    Never raises — on failure it sets the degradation flag and lets the service come up
    serving /health, because a service you can curl is one you can diagnose.
    """
    global _migration_failed

    from alembic import command
    from alembic.config import Config as AlembicConfig

    ini_path = os.getenv("ALEMBIC_INI", "alembic.ini")
    if not os.path.exists(ini_path):
        logger.error("No alembic.ini at %s — cannot migrate. Schema may be missing.", ini_path)
        _migration_failed = True
        return False

    try:
        alembic_cfg = AlembicConfig(ini_path)
        # The URL comes from app.config, never from alembic.ini, so there is exactly
        # one place that decides which database this service talks to.
        alembic_cfg.set_main_option("sqlalchemy.url", _sync_url(config.database_url()))
        # Keep our logging. env.py honours this flag and skips fileConfig, which would
        # otherwise reconfigure the ROOT logger from alembic.ini and mute every app.*
        # line for the rest of the process — including this function's own error log.
        alembic_cfg.attributes["configure_logger"] = False
        command.upgrade(alembic_cfg, "head")
        _migration_failed = False
        logger.info("Database migrations are at head.")
        return True
    except Exception as exc:
        _migration_failed = True
        logger.error(
            "MIGRATION FAILED: %s — every route except /health will return 503. "
            "The service is up so you can read this; fix the migration and restart.",
            exc,
        )
        return False


def _sync_url(url: str) -> str:
    """Alembic runs migrations on a synchronous driver. Strip the async dialect.

    Keeping one URL in config and deriving the sync form here means postgres later is
    still a single line change (`+asyncpg` -> `+psycopg`), not two that can disagree.
    """
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _scrub_url(url: str) -> str:
    """Never log a URL with credentials in it — postgres URLs carry a password."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


async def healthcheck() -> bool:
    """Cheap 'can we actually talk to the database' probe for /health."""
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database healthcheck failed: %s", exc)
        return False
