"""auth sessions: one row per signed-in device

Revision ID: f3b8e1a6c2d4
Revises: d2a7c4e9f1b3
Created: 2026-10-15 16:00:00.000000

A new table, filled from ``refresh_tokens``: one row per family, so every
device already signed in keeps working (``app/deps.py`` refuses a token whose device has
no row) and shows up in the list straight away. No existing row is changed or removed.

Per family: signed in at its first token, last seen at its newest, where from and on
what as its newest says, and revoked if its newest is.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b8e1a6c2d4'
down_revision: str | None = 'd2a7c4e9f1b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=256), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auth_sessions_user_id'), ['user_id'], unique=False)

    # Window functions rather than GROUP BY: the newest row's ip and user agent are
    # wanted, and a bare column beside min() and max() is whichever row the database
    # likes. Works on sqlite 3.25+ and on postgres alike.
    op.execute(
        """
        INSERT INTO auth_sessions
            (id, user_id, created_at, last_seen_at, expires_at,
             ip, user_agent, revoked_at, revoked_reason)
        SELECT family_id, user_id, first_issued, issued_at, family_expires_at,
               ip, user_agent, revoked_at, revoked_reason
        FROM (
            SELECT family_id, user_id, issued_at, family_expires_at,
                   ip, user_agent, revoked_at, revoked_reason,
                   MIN(issued_at) OVER (PARTITION BY family_id) AS first_issued,
                   ROW_NUMBER() OVER (
                       PARTITION BY family_id ORDER BY issued_at DESC, id DESC
                   ) AS newest
            FROM refresh_tokens
        ) AS ranked
        WHERE newest = 1
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_sessions_user_id'))
    op.drop_table('auth_sessions')
