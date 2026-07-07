"""add team tables (R2.1 Team Foundations)

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-07-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'o9p0q1r2s3t4'
down_revision = 'n8o9p0q1r2s3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user',
        sa.Column('is_plus', sa.Boolean(), nullable=False, server_default=sa.false())
    )

    op.create_table(
        'team',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'team_membership',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', 'user_id', name='uq_team_membership'),
    )
    op.create_index('ix_team_membership_user_id', 'team_membership', ['user_id'])

    op.create_table(
        'team_invite_code',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=8), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('rotated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', name='uq_team_invite_code_team'),
        sa.UniqueConstraint('code', name='uq_team_invite_code_code'),
    )

    op.create_table(
        'team_message',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('sender_type', sa.String(length=10), nullable=False),
        sa.Column('sender_user_id', sa.Integer(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.ForeignKeyConstraint(['sender_user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_team_message_team_id', 'team_message', ['team_id'])

    op.create_table(
        'team_moment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('moment_type', sa.String(length=40), nullable=False),
        sa.Column('subject_user_id', sa.Integer(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('moment_metadata', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.ForeignKeyConstraint(['subject_user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_team_moment_team_id', 'team_moment', ['team_id'])

    op.create_table(
        'team_campfire',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('total_team_missions', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', name='uq_team_campfire_team'),
    )


def downgrade():
    op.drop_table('team_campfire')
    op.drop_index('ix_team_moment_team_id', table_name='team_moment')
    op.drop_table('team_moment')
    op.drop_index('ix_team_message_team_id', table_name='team_message')
    op.drop_table('team_message')
    op.drop_table('team_invite_code')
    op.drop_index('ix_team_membership_user_id', table_name='team_membership')
    op.drop_table('team_membership')
    op.drop_table('team')
    op.drop_column('user', 'is_plus')
