"""pick session store: session_provider_id and session_id on selected_models

Revision ID: f7c2e8a4d1b6
Revises: e5b1d7a3c920
Created: 2026-10-04 16:00:00.000000

A pick can keep its thread in a session store (a memory-mcp-ce server added as a
`session` provider). Two nullable columns: the store, and an optional session id the
user typed to join an existing session. Null for both is what every pick had before,
so nothing changes until somebody chooses.

`session` as a new value of ProviderKind and PickHistory needs no DDL: both enums are
non-native (VARCHAR(16), no CHECK constraint), so the new values are just new strings.

Hand-written for ``ondelete="SET NULL"``, for the reason given in b7e2d41f9c05: deleting
a store must not delete the user's model selections. batch_alter_table because sqlite
cannot ALTER a table to add a foreign key.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c2e8a4d1b6'
down_revision: str | None = 'e5b1d7a3c920'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('session_provider_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('session_id', sa.String(length=128), nullable=True))
        batch_op.create_foreign_key(
            'fk_selected_models_session_provider',
            'provider_secrets',
            ['session_provider_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('selected_models', schema=None) as batch_op:
        batch_op.drop_constraint('fk_selected_models_session_provider', type_='foreignkey')
        batch_op.drop_column('session_id')
        batch_op.drop_column('session_provider_id')
