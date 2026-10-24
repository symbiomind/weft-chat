"""wefts: selected_models becomes a table of named wefts that own their config

Revision ID: 2e93d99f1f9a
Revises: c1f5b8d2e4a7
Created: 2026-10-08 12:00:00.000000

A pick used to BE its (provider, model) pair: ``provider_id`` was NOT NULL
with ON DELETE CASCADE, and unique with ``model_id``. Now a weft is a row of its own,
owned by a user, with a name, and the pair is a binding that may be null.

Built as a new table and copied into, rather than altered in place. Every constraint
changes: the provider FK goes from CASCADE to SET NULL, both binding columns become
nullable, the old unique pair goes and a unique (user, name) arrives. In batch mode
that is a rebuild either way, and a rebuild written out here says exactly what the
result is, where an alter of an unnamed sqlite FK would depend on reflection.

Every existing row becomes a weft with the same id and every setting it had:

* ``user_id`` from its provider's owner.
* ``name`` from the last segment of the model id (``bridge/iris`` -> ``Iris``), with
  `` 2``, `` 3`` added when one user would otherwise have two of the same name. Rename
  them on the weft page.
* ``auto_session_id`` is the session id the row was ALREADY using when nobody typed one:
  ``weft.<user>.<first 8 of provider id>.<model id with / as .>``, computed by the rule
  app/sessions.py had until now. Copying it is what keeps a thread kept in a session
  store attached to its weft, on whatever engine the weft is bound to later.

Foreign keys are off while this runs and checked afterwards (alembic/env.py).
"""
from collections.abc import Sequence
import re

from alembic import op
import sqlalchemy as sa


revision: str = '2e93d99f1f9a'
down_revision: str | None = 'c1f5b8d2e4a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


HISTORY = sa.Enum('memory', 'local', 'session', native_enum=False, length=16,
                  name='pickhistory')

#: Every settings column the two tables share, copied as they stand.
SETTINGS = (
    'tts_provider_id', 'tts_model', 'tts_voice', 'history', 'send_turns', 'window_turns',
    'colour_a', 'colour_b', 'session_provider_id', 'session_id', 'background', 'headers',
)

_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]+")


def old_derived_session_id(username: str, provider_id: str, model_id: str) -> str:
    """app/sessions.py's rule before this revision. Frozen here, not imported: the app's
    rule has changed, and a migration must keep meaning what it meant when written."""
    head = f"weft.{_UNSAFE.sub('.', username)}.{provider_id[:8]}."
    tail = _UNSAFE.sub(".", model_id).strip(".") or "model"
    return (head + tail)[:128]


def name_from_model(model_id: str) -> str:
    tail = model_id.rstrip("/").rsplit("/", 1)[-1].strip() or model_id.strip() or "Weft"
    return (tail[:1].upper() + tail[1:])[:60]


def name_key(name: str) -> str:
    """app.models.weft_name_key, frozen for the same reason."""
    return " ".join(name.split()).casefold()


def settings_columns() -> list[sa.Column]:
    return [
        sa.Column('tts_provider_id', sa.String(length=36), nullable=True),
        sa.Column('tts_model', sa.String(length=128), nullable=True),
        sa.Column('tts_voice', sa.String(length=128), nullable=True),
        sa.Column('history', HISTORY, nullable=False, server_default='memory'),
        sa.Column('send_turns', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('window_turns', sa.Integer(), nullable=False, server_default='200'),
        sa.Column('colour_a', sa.String(length=7), nullable=True),
        sa.Column('colour_b', sa.String(length=7), nullable=True),
        sa.Column('session_provider_id', sa.String(length=36), nullable=True),
        sa.Column('session_id', sa.String(length=128), nullable=True),
        sa.Column('background', sa.String(length=64), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        'wefts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('name_key', sa.String(length=64), nullable=False),
        sa.Column('provider_id', sa.String(length=36), nullable=True),
        sa.Column('model_id', sa.String(length=256), nullable=True),
        sa.Column('auto_session_id', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        *settings_columns(),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE',
                                name='fk_wefts_user'),
        sa.ForeignKeyConstraint(['provider_id'], ['provider_secrets.id'],
                                ondelete='SET NULL', name='fk_wefts_provider'),
        sa.ForeignKeyConstraint(['tts_provider_id'], ['provider_secrets.id'],
                                ondelete='SET NULL', name='fk_wefts_tts_provider'),
        sa.ForeignKeyConstraint(['session_provider_id'], ['provider_secrets.id'],
                                ondelete='SET NULL', name='fk_wefts_session_provider'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name_key', name='uq_weft_user_name'),
    )
    op.create_index('ix_wefts_user_id', 'wefts', ['user_id'], unique=False)
    op.create_index('ix_wefts_provider_id', 'wefts', ['provider_id'], unique=False)

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT s.id, s.provider_id, s.model_id, s.created_at, p.user_id, u.username, "
        + ", ".join(f"s.{c}" for c in SETTINGS)
        + " FROM selected_models s"
        " JOIN provider_secrets p ON p.id = s.provider_id"
        " JOIN users u ON u.id = p.user_id"
        " ORDER BY u.username, s.created_at, s.id"
    )).mappings().all()

    taken: dict[str, set[str]] = {}
    insert = sa.text(
        "INSERT INTO wefts (id, user_id, name, name_key, provider_id, model_id,"
        " auto_session_id, created_at, " + ", ".join(SETTINGS) + ")"
        " VALUES (:id, :user_id, :name, :name_key, :provider_id, :model_id,"
        " :auto_session_id, :created_at, " + ", ".join(f":{c}" for c in SETTINGS) + ")"
    )
    for row in rows:
        used = taken.setdefault(row['user_id'], set())
        base = name_from_model(row['model_id'])
        name, n = base, 1
        while name_key(name) in used:
            n += 1
            name = f"{base} {n}"
        used.add(name_key(name))

        bind.execute(insert, {
            'id': row['id'],
            'user_id': row['user_id'],
            'name': name,
            'name_key': name_key(name),
            'provider_id': row['provider_id'],
            'model_id': row['model_id'],
            'auto_session_id': old_derived_session_id(
                row['username'], row['provider_id'], row['model_id']),
            'created_at': row['created_at'],
            **{c: row[c] for c in SETTINGS},
        })

    copied = bind.execute(sa.text("SELECT count(*) FROM wefts")).scalar_one()
    before = bind.execute(sa.text("SELECT count(*) FROM selected_models")).scalar_one()
    if copied != before:
        # Only a pick whose provider or user had already gone could miss the join, and
        # the foreign keys say there are none. If there are, stop rather than lose them.
        raise RuntimeError(
            f"Copied {copied} of {before} picks into wefts; refusing to drop the rest."
        )

    op.drop_index('ix_selected_models_provider_id', table_name='selected_models')
    op.drop_table('selected_models')


def downgrade() -> None:
    """Back to picks. Only bound wefts can be picks, and only one per (provider, model):
    an unbound weft, or a second weft on the same engine, has no shape to go back to and
    is dropped. Names are dropped with the column."""
    op.create_table(
        'selected_models',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('provider_id', sa.String(length=36), nullable=False),
        sa.Column('model_id', sa.String(length=256), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        *settings_columns(),
        sa.ForeignKeyConstraint(['provider_id'], ['provider_secrets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tts_provider_id'], ['provider_secrets.id'],
                                ondelete='SET NULL', name='fk_selected_models_tts_provider'),
        sa.ForeignKeyConstraint(['session_provider_id'], ['provider_secrets.id'],
                                ondelete='SET NULL', name='fk_selected_models_session_provider'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_id', 'model_id', name='uq_selected_provider_model'),
    )
    op.create_index('ix_selected_models_provider_id', 'selected_models', ['provider_id'],
                    unique=False)

    columns = "id, provider_id, model_id, created_at, " + ", ".join(SETTINGS)
    op.execute(
        f"INSERT INTO selected_models ({columns})"
        f" SELECT {columns} FROM wefts w"
        " WHERE w.provider_id IS NOT NULL AND w.model_id IS NOT NULL"
        " AND w.id = (SELECT w2.id FROM wefts w2 WHERE w2.provider_id = w.provider_id"
        " AND w2.model_id = w.model_id ORDER BY w2.created_at, w2.id LIMIT 1)"
    )

    op.drop_index('ix_wefts_provider_id', table_name='wefts')
    op.drop_index('ix_wefts_user_id', table_name='wefts')
    op.drop_table('wefts')
