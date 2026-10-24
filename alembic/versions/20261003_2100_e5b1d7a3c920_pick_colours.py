"""pick colours: colour_a and colour_b on selected_models

Revision ID: e5b1d7a3c920
Revises: d4a9c62f1b78
Created: 2026-10-03 21:00:00.000000

Two nullable columns and nothing else. Null is "the theme's own pair", which is what
every pick wore before this migration, so no existing row needs translating and nothing
on screen changes until somebody chooses.

batch_alter_table for the same reason as the migrations before it: sqlite rebuilds
rather than ALTERs.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'e5b1d7a3c920'
down_revision: str | None = 'd4a9c62f1b78'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('colour_a', sa.String(length=7), nullable=True))
        batch_op.add_column(sa.Column('colour_b', sa.String(length=7), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_column('colour_b')
        batch_op.drop_column('colour_a')
