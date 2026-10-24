"""give a pick a voice

Revision ID: b7e2d41f9c05
Revises: a1c4f7e9b2d3
Created: 2026-09-14 18:30:00.000000

Three nullable columns on selected_models. They move together: null means the thread is
silent, which is the default and the only sensible one — a window should not start
making noise because a migration ran.

Hand-written, and the reason is ``ondelete="SET NULL"`` on the new foreign key.
Autogenerate would have produced a plain reference, and the default behaviour would then
block deleting any speech provider a pick had ever used. CASCADE would be worse: it
would delete the user's *model selections* because they deleted a *voice*. Nulling is
the honest middle — the thread survives and simply stops speaking.

sqlite cannot ALTER a table to add a foreign key, so this runs inside batch_alter_table,
which rebuilds the table. That is why the column definitions here are complete rather
than incremental.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e2d41f9c05'
down_revision: str | None = 'a1c4f7e9b2d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tts_provider_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('tts_model', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('tts_voice', sa.String(length=128), nullable=True))
        batch_op.create_foreign_key(
            'fk_selected_models_tts_provider',
            'provider_secrets',
            ['tts_provider_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_constraint('fk_selected_models_tts_provider', type_='foreignkey')
        batch_op.drop_column('tts_voice')
        batch_op.drop_column('tts_model')
        batch_op.drop_column('tts_provider_id')
