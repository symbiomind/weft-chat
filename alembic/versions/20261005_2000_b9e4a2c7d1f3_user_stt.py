"""user stt: push-to-talk settings on users

Revision ID: b9e4a2c7d1f3
Revises: a8d3f1c6e2b9
Created: 2026-10-05 20:00:00.000000

Push to talk has one speech-to-text provider per user, so its settings
live on the user rather than on a pick. Null provider is "no push to talk", which is
what every user had before this migration. `stt` as a new value of ProviderKind needs
no DDL: the enum is stored as a plain string.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b9e4a2c7d1f3'
down_revision: str | None = 'a8d3f1c6e2b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stt_provider_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('stt_model', sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column('stt_language', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column(
            'stt_send_on_release', sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch_op.add_column(sa.Column(
            'stt_tell_buddy', sa.Boolean(), nullable=False, server_default=sa.true(),
        ))
        batch_op.create_foreign_key(
            'fk_users_stt_provider',
            'provider_secrets',
            ['stt_provider_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_stt_provider', type_='foreignkey')
        batch_op.drop_column('stt_tell_buddy')
        batch_op.drop_column('stt_send_on_release')
        batch_op.drop_column('stt_language')
        batch_op.drop_column('stt_model')
        batch_op.drop_column('stt_provider_id')
