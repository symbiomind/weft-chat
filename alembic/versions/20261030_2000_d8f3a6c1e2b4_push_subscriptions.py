"""push subscriptions: one row per device that asked to be rung while Weft is closed

Revision ID: d8f3a6c1e2b4
Revises: c7a2e5d9f3b1
Created: 2026-10-30 20:00:00.000000

A new table and nothing else: no existing row is touched.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd8f3a6c1e2b4'
down_revision: str | None = 'c7a2e5d9f3b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('endpoint', sa.String(length=1024), nullable=False),
        sa.Column('p256dh', sa.String(length=128), nullable=False),
        sa.Column('auth', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['session_id'], ['auth_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint'),
    )
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_push_subscriptions_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_push_subscriptions_session_id'), ['session_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_push_subscriptions_session_id'))
        batch_op.drop_index(batch_op.f('ix_push_subscriptions_user_id'))
    op.drop_table('push_subscriptions')
