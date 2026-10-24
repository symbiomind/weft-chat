"""tip marks: tips progress kept with the account

Revision ID: a4c9e2f7b1d5
Revises: f3b8e1a6c2d4
Created: 2026-10-16 22:00:00.000000

Until now the browser kept which tips were done, per device. That made
the hidden ones, which are found rather than read, easy to lose to a new phone, and
left the server unable to answer "has this account found X" when a find unlocks
something. A new table, one row per finished tip, and a column on users for the
switch. Nothing is copied over: progress in a browser starts again from the beginning.

The column is added outside batch mode, so sqlite takes it as a plain ALTER TABLE ADD
COLUMN and ``users`` is not rebuilt (a rebuild of ``users`` once cost every provider).
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a4c9e2f7b1d5'
down_revision: str | None = 'f3b8e1a6c2d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Not in a batch: batch mode rebuilds `users` for a column with a server default,
    # and sqlite can add this one in place.
    op.add_column('users', sa.Column(
        'tips_on', sa.Boolean(), nullable=False, server_default=sa.true(),
    ))
    op.create_table(
        'tip_marks',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('tip_id', sa.String(length=128), nullable=False),
        sa.Column('since', sa.String(length=32), nullable=False),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'tip_id', 'since'),
    )


def downgrade() -> None:
    op.drop_table('tip_marks')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('tips_on')
