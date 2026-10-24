"""pick background: one nullable column on selected_models

Revision ID: a8d3f1c6e2b9
Revises: f7c2e8a4d1b6
Created: 2026-10-04 20:00:00.000000

The hash of the pick's background picture. Null is "no picture", which is what every
pick had before this migration, so nothing on screen changes until somebody uploads.
The picture itself lives on disk under data/backgrounds/, not in the database.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a8d3f1c6e2b9'
down_revision: str | None = 'f7c2e8a4d1b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('background', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_column('background')
