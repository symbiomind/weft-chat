"""pick headers: one nullable JSON column on selected_models

Revision ID: c1f5b8d2e4a7
Revises: b9e4a2c7d1f3
Created: 2026-10-06 09:00:00.000000

Extra HTTP headers a pick sends with its chat requests. Null is "none", which is what
every pick had before this migration, so nothing that goes upstream changes until
somebody adds one.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c1f5b8d2e4a7'
down_revision: str | None = 'b9e4a2c7d1f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('headers', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_column('headers')
