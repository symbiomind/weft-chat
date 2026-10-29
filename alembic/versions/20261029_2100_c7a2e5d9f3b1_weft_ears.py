"""weft ears: a weft can choose its own push-to-talk provider, or none

Revision ID: c7a2e5d9f3b1
Revises: ada7b0125884
Created: 2026-10-29 21:00:00.000000

Three columns on wefts. ``stt_mode`` defaults to ``account``, which is what every weft
did before this column existed, so the migration changes nothing for anyone until they
ask it to.

Plain ALTER TABLE ... ADD COLUMN, never batch mode: a rebuild of a table once cost every
provider. sqlite adds a column with a REFERENCES clause in place as long as its default
is null, and postgres takes the same statement. Written as SQL because alembic's
add_column would try to add the foreign key separately, which sqlite cannot do.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c7a2e5d9f3b1'
down_revision: str | None = 'ada7b0125884'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('wefts', sa.Column(
        'stt_mode', sa.String(length=16), nullable=False, server_default='account',
    ))
    op.execute(
        'ALTER TABLE wefts ADD COLUMN stt_provider_id VARCHAR(36)'
        ' REFERENCES provider_secrets (id) ON DELETE SET NULL'
    )
    op.add_column('wefts', sa.Column('stt_model', sa.String(length=256), nullable=True))


def downgrade() -> None:
    # sqlite 3.35 and later drop a plain column in place. The one with a foreign key
    # cannot be, so the downgrade rebuilds; downgrades run by hand, on a backup.
    with op.batch_alter_table('wefts', schema=None) as batch_op:
        batch_op.drop_column('stt_model')
        batch_op.drop_column('stt_provider_id')
        batch_op.drop_column('stt_mode')
