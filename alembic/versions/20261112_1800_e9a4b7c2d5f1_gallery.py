"""gallery: what each picture and file is, and which wefts have been shown it

Revision ID: e9a4b7c2d5f1
Revises: d8f3a6c1e2b4
Created: 2026-11-12 18:00:00.000000

Two new tables and nothing else: no existing row is touched, and no file on disk is.
Pictures already uploaded get their gallery row the first time anything asks about
them (app/gallery.py ``adopt``), so this migration never reads the attachments folder.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'e9a4b7c2d5f1'
down_revision: str | None = 'd8f3a6c1e2b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'gallery_items',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('hash', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('mime', sa.String(length=100), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=True),
        sa.Column('weft_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['weft_id'], ['wefts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('user_id', 'hash'),
    )
    op.create_table(
        'gallery_shares',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('hash', sa.String(length=64), nullable=False),
        sa.Column('weft_id', sa.String(length=36), nullable=False),
        sa.Column('shared_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'hash'],
                                ['gallery_items.user_id', 'gallery_items.hash'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['weft_id'], ['wefts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'hash', 'weft_id'),
    )
    with op.batch_alter_table('gallery_shares', schema=None) as batch_op:
        batch_op.create_index('ix_gallery_shares_weft_id', ['weft_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('gallery_shares', schema=None) as batch_op:
        batch_op.drop_index('ix_gallery_shares_weft_id')
    op.drop_table('gallery_shares')
    op.drop_table('gallery_items')
