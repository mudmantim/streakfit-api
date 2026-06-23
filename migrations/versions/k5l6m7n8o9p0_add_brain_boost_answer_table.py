"""add brain_boost_answer table

Revision ID: k5l6m7n8o9p0
Revises: j4l5m6n7o8p9
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'k5l6m7n8o9p0'
down_revision = 'j4l5m6n7o8p9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'brain_boost_answer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('correct', sa.Boolean(), nullable=False),
        sa.Column('points_earned', sa.Integer(), nullable=False),
        sa.Column('answered_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', name='uq_brain_boost_answer'),
    )


def downgrade():
    op.drop_table('brain_boost_answer')
