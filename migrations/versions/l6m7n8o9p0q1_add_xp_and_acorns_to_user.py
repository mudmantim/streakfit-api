"""add xp_total and acorns_total to user

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'l6m7n8o9p0q1'
down_revision = 'k5l6m7n8o9p0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user',
        sa.Column('xp_total', sa.Integer(), nullable=False, server_default='0')
    )
    op.add_column('user',
        sa.Column('acorns_total', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade():
    op.drop_column('user', 'acorns_total')
    op.drop_column('user', 'xp_total')
