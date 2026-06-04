"""v0.6.1 add skill_level to user and create daily_completion table

Revision ID: f9e2a1b3c8d7
Revises: a3f8b1c2d4e5
Create Date: 2026-06-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f9e2a1b3c8d7'
down_revision = 'a3f8b1c2d4e5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user',
        sa.Column('skill_level', sa.String(20), nullable=False, server_default='beginner')
    )

    op.create_table(
        'daily_completion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('exercise_key', sa.String(100), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', 'exercise_key', name='uq_daily_completion'),
    )
    op.create_index(
        'ix_daily_completion_user_date',
        'daily_completion',
        ['user_id', 'date'],
        unique=False
    )


def downgrade():
    op.drop_index('ix_daily_completion_user_date', table_name='daily_completion')
    op.drop_table('daily_completion')
    op.drop_column('user', 'skill_level')
