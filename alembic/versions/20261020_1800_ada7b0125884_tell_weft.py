"""tell_weft: the push-to-talk "say it was spoken" switch takes the product's name

Revision ID: ada7b0125884
Revises: b6e1d3f8a2c7
Created: 2026-10-20 18:00:00.000000

users.stt_tell_buddy becomes users.stt_tell_weft, matching the API field it backs.
Every value is kept.

A plain ALTER TABLE ... RENAME COLUMN, never batch mode: batch mode rebuilds ``users``,
and a rebuild of ``users`` once cost every provider. sqlite (3.25 and later) and
postgres both rename a column in place.
"""
from collections.abc import Sequence

from alembic import op


revision: str = 'ada7b0125884'
down_revision: str | None = 'b6e1d3f8a2c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('ALTER TABLE users RENAME COLUMN stt_tell_buddy TO stt_tell_weft')


def downgrade() -> None:
    op.execute('ALTER TABLE users RENAME COLUMN stt_tell_weft TO stt_tell_buddy')
