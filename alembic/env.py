"""
Alembic environment for weft-service.

The URL is taken from ``app.config`` (falling back to whatever ``db.run_migrations``
set on the config object), never from a ``sqlalchemy.url`` line in alembic.ini. One
source of truth for which database this service talks to.

``render_as_batch`` is on because the target is sqlite: sqlite cannot ALTER a column or
drop a constraint in place, so Alembic has to rebuild the table. Without batch mode,
the first migration that alters a column fails — and on an auth schema, altering
columns is a matter of when, not if.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import config as weft_config
from app.models import Base

alembic_config = context.config

# Only configure logging from alembic.ini when Alembic is driven from the command
# line. When the application calls `alembic upgrade head` in-process at startup it
# sets configure_logger=False (Alembic's own documented idiom), because fileConfig
# would otherwise take over the *root* logger — applying alembic.ini's
# `[logger_root] level = WARNING` and replacing its handlers.
#
# The symptom that taught us this: every app.* INFO line vanished from the moment the
# first migration ran. The service looked like it hung at "Running upgrade" — startup
# lines, the "listening" banner, and (worst) the migration's own failure message all
# went to a root logger that had just been muted. A service whose logs disappear
# exactly when something goes wrong is not debuggable.
#
# disable_existing_loggers=False is kept for the CLI path for the same family of
# reasons: the default (True) would disable every already-created app logger outright.
if (
    alembic_config.config_file_name is not None
    and alembic_config.attributes.get("configure_logger", True)
):
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Render ``UTCDateTime`` columns as plain ``sa.DateTime(timezone=True)``.

    Autogenerate's default is to emit the Python type it found —
    ``app.models.UTCDateTime(timezone=True)`` — which forces every migration to import
    application code. That is a well-known footgun: migrations are a historical record
    that must still run years later, and one that imports ``app.models`` breaks the
    moment that module is renamed, refactored, or deleted. A migration should describe
    the *database*, and at the database level UTCDateTime IS a timezone-aware DateTime
    — the decorator only does work on the Python side.

    Returning False falls through to Alembic's normal rendering for everything else.
    """
    if type_ == "type" and obj.__class__.__name__ == "UTCDateTime":
        # No imports.add here: script.py.mako already imports sqlalchemy as sa, and
        # adding it again just emits the line twice.
        return "sa.DateTime(timezone=True)"
    return False


def _database_url() -> str:
    """Prefer the URL the caller set on the Alembic config (that is
    ``db.run_migrations`` handing us the already-derived sync URL); otherwise read
    app config directly, so `alembic` on the command line works too."""
    configured = alembic_config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured

    weft_config.load_config()
    url = weft_config.database_url()
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = alembic_config.get_section(alembic_config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # FOREIGN KEYS ARE OFF WHILE MIGRATING, and checked afterwards. They used to be
        # ON, to stop a batch rebuild quietly orphaning rows, and that was the opposite
        # of safe: batch mode rebuilds a table by creating a copy, dropping the original
        # and renaming the copy, and with enforcement on, DROP TABLE fires every
        # ON DELETE CASCADE that points at it. Rebuilding `users` (2026-10-05, the
        # push-to-talk columns) deleted every provider and, through them, every pick.
        # A leaf table like selected_models had been rebuilt before without harm, which
        # is how the trap stayed hidden.
        #
        # This is sqlite's own documented procedure for altering a table: enforcement
        # off, alter, then `PRAGMA foreign_key_check` to prove nothing was orphaned. The
        # check below is what the old ON was meant to buy, without the cascade.
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()

        if sqlite:
            orphans = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if orphans:
                raise RuntimeError(
                    f"Migration left {len(orphans)} row(s) pointing at rows that do not "
                    f"exist (first: {orphans[0]}). Restore the database from before it "
                    f"and fix the migration."
                )
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")

        # Explicit commit. SQLAlchemy 2.0's connect() opens an implicit transaction
        # that is ROLLED BACK on close unless something commits it, and on sqlite
        # Alembic runs in "non-transactional DDL" mode where begin_transaction() is
        # effectively a no-op.
        #
        # The resulting bug is genuinely nasty and looks like success: sqlite's driver
        # auto-commits DDL, so CREATE TABLE survives — but the INSERT into
        # alembic_version is DML, so the stamp is rolled back. You get a fully built
        # schema that Alembic believes is at revision zero, so the NEXT boot re-runs
        # the first migration and dies on "table already exists". Verified here by
        # watching alembic_version come back empty after a successful-looking upgrade.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
