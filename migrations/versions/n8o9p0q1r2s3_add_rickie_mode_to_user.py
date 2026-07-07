"""add rickie_mode to user

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-07-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'n8o9p0q1r2s3'
down_revision = 'm7n8o9p0q1r2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user',
        sa.Column('rickie_mode', sa.String(20), nullable=False, server_default='full')
    )


def downgrade():
    op.drop_column('user', 'rickie_mode')
