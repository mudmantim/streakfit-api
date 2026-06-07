"""add push_subscription table

Revision ID: i3k4l5m6n7o8
Revises: h2j3k4l5m6n7
Create Date: 2026-06-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'i3k4l5m6n7o8'
down_revision = 'h2j3k4l5m6n7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'push_subscription',
        sa.Column('id',         sa.Integer(),     primary_key=True),
        sa.Column('user_id',    sa.Integer(),     sa.ForeignKey('user.id'), nullable=False),
        sa.Column('endpoint',   sa.Text(),        nullable=False, unique=True),
        sa.Column('auth',       sa.String(256),   nullable=False),
        sa.Column('p256dh',     sa.String(512),   nullable=False),
        sa.Column('created_at', sa.DateTime(),    nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_push_subscription_user', 'push_subscription', ['user_id'])


def downgrade():
    op.drop_index('ix_push_subscription_user', table_name='push_subscription')
    op.drop_table('push_subscription')
