"""add display_mode to user

Revision ID: g6h7i8j9k0l1
Revises: f9e2a1b3c8d7
Create Date: 2026-06-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'g6h7i8j9k0l1'
down_revision = 'f9e2a1b3c8d7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user',
        sa.Column('display_mode', sa.String(20), nullable=False, server_default='game')
    )


def downgrade():
    op.drop_column('user', 'display_mode')
