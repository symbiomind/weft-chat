"""the history dials: send_turns and window_turns replace the send enum

Revision ID: d4a9c62f1b78
Revises: c3f8a15d7e42
Created: 2026-09-23 21:00:00.000000

WHY AN INTEGER AND NOT ANOTHER ENUM VALUE.

The panel above these columns shows a beginner a sentence — "the last 20 turns", "this
turn only" — and shows Advanced a number. Those are not two settings. They are one value
rendered twice, and that is the entire reason `send` stopped being an enum: an enum
beside a number is two fields that can disagree, and something then has to decide which
one wins. Whatever it decides, it is ignoring what somebody typed.

WHAT HAPPENS TO AN EXISTING PICK.

`send` -> `send_turns` is a faithful translation, done row by row before the old column
is dropped:

    everything -> 0   (no cap beyond the window)
    last       -> 1   (the current turn alone)

So a pick that was explicitly set to `last` still sends one turn, and the new default of
20 applies only to picks made after this lands. c3f8a15d7e42's docstring set that rule
for anything touching what goes upstream and it still holds: an explicit choice is not a
thing a migration gets to revise.

BUT `window_turns` IS NOT NEUTRAL, and pretending otherwise would be the dishonest
version of this note. Every existing row gets 200. Before this migration the window was
unbounded — not by decision, but because no column existed to bound it. A thread longer
than 200 turns will therefore lose its oldest turns from the device. Two reasons that is
the right call rather than a regression:

  * Unbounded was never a promise. It was the absence of a feature, and the absence was
    the bug: it was reported three times.
  * `send_turns = 0` now means "everything the window holds", so a translated pick that
    used to say `everything` is also capped at 200 turns on the wire. For anybody that
    actually affects, being charged for 200 turns instead of 4,000 is the mercy, not the
    loss.

Anyone who wants the old behaviour has a dial to raise, which is more than they had
yesterday.

batch_alter_table for the same reason as c3f8a15d7e42: sqlite rebuilds rather than
ALTERs, and it is also the only way to drop a column here.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a9c62f1b78'
down_revision: str | None = 'c3f8a15d7e42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEND = sa.Enum('everything', 'last', native_enum=False, length=16, name='picksend')

DEFAULT_SEND_TURNS = 20
DEFAULT_WINDOW_TURNS = 200


def upgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'send_turns',
                sa.Integer(),
                nullable=False,
                server_default=str(DEFAULT_SEND_TURNS),
            )
        )
        batch_op.add_column(
            sa.Column(
                'window_turns',
                sa.Integer(),
                nullable=False,
                server_default=str(DEFAULT_WINDOW_TURNS),
            )
        )

    # Translate before dropping. Every existing row has a `send` value (it was NOT NULL
    # with a server default), so this reaches all of them and the new column's default
    # of 20 survives only for rows created from here on — which is the point.
    op.execute("UPDATE selected_models SET send_turns = 0 WHERE send = 'everything'")
    op.execute("UPDATE selected_models SET send_turns = 1 WHERE send = 'last'")

    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_column('send')


def downgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('send', SEND, nullable=False, server_default='everything')
        )

    # The reverse translation cannot be faithful and should not pretend to be: the enum
    # has two values and the column it is coming back from has ten thousand. Anything
    # that is not exactly 1 becomes `everything`, because sending MORE than was asked is
    # a visible cost, while sending less is a model that has quietly lost its memory and
    # nothing on screen to say why.
    op.execute("UPDATE selected_models SET send = 'last' WHERE send_turns = 1")
    op.execute("UPDATE selected_models SET send = 'everything' WHERE send_turns <> 1")

    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_column('window_turns')
        batch_op.drop_column('send_turns')
