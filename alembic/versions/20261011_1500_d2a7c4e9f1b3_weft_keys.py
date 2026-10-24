"""weft keys: one table for the MCP door's per-weft keys

Revision ID: d2a7c4e9f1b3
Revises: 2e93d99f1f9a
Created: 2026-10-11 15:00:00.000000

A new table and nothing else: no existing row is touched, so a weft has
no key, and the door stays shut, until somebody mints one.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd2a7c4e9f1b3'
down_revision: str | None = '2e93d99f1f9a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'weft_keys',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('weft_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('hint', sa.String(length=8), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['weft_id'], ['wefts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('weft_id'),
    )
    with op.batch_alter_table('weft_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_weft_keys_token_hash'), ['token_hash'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('weft_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_weft_keys_token_hash'))
    op.drop_table('weft_keys')
