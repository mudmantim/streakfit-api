"""add analytics_event table

Revision ID: h2j3k4l5m6n7
Revises: g6h7i8j9k0l1
Create Date: 2026-06-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'h2j3k4l5m6n7'
down_revision = 'g6h7i8j9k0l1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'analytics_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_name', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_analytics_event_name_date',
                    'analytics_event', ['event_name', 'created_at'])


def downgrade():
    op.drop_index('ix_analytics_event_name_date', table_name='analytics_event')
    op.drop_table('analytics_event')
