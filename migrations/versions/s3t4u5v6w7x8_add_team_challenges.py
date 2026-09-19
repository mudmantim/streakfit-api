"""Add team challenges.

One person nudging another to move, from a fixed preset list. The challenge
hangs off the existing team thread via `team_message.challenge_id`, the same way
a photo does, so there is one feed with one ordering rather than a second one.

Completions are their own table rather than a status column because a
whole-team challenge is completed by several people independently, and because
there is deliberately no "failed" row — a challenge nobody does simply expires.

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-09-18
"""
import sqlalchemy as sa
from alembic import op

revision = 's3t4u5v6w7x8'
down_revision = 'r2s3t4u5v6w7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'team_challenge',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        # Nullable so account deletion can drop the link without breaking the
        # card, exactly as it does for a message's author.
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('target_user_id', sa.Integer(), nullable=True),
        sa.Column('preset_key', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['target_user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_team_challenge_public_id'), 'team_challenge',
                    ['public_id'], unique=True)
    op.create_index(op.f('ix_team_challenge_team_id'), 'team_challenge',
                    ['team_id'], unique=False)
    op.create_index(op.f('ix_team_challenge_created_at'), 'team_challenge',
                    ['created_at'], unique=False)

    op.create_table(
        'team_challenge_completion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('challenge_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['challenge_id'], ['team_challenge.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('challenge_id', 'user_id', name='uq_challenge_completion'),
    )
    op.create_index(op.f('ix_team_challenge_completion_challenge_id'),
                    'team_challenge_completion', ['challenge_id'], unique=False)
    op.create_index(op.f('ix_team_challenge_completion_user_id'),
                    'team_challenge_completion', ['user_id'], unique=False)

    with op.batch_alter_table('team_message') as batch:
        batch.add_column(sa.Column('challenge_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_team_message_challenge', 'team_challenge',
                                 ['challenge_id'], ['id'])


def downgrade():
    with op.batch_alter_table('team_message') as batch:
        batch.drop_constraint('fk_team_message_challenge', type_='foreignkey')
        batch.drop_column('challenge_id')

    op.drop_index(op.f('ix_team_challenge_completion_user_id'),
                  table_name='team_challenge_completion')
    op.drop_index(op.f('ix_team_challenge_completion_challenge_id'),
                  table_name='team_challenge_completion')
    op.drop_table('team_challenge_completion')

    op.drop_index(op.f('ix_team_challenge_created_at'), table_name='team_challenge')
    op.drop_index(op.f('ix_team_challenge_team_id'), table_name='team_challenge')
    op.drop_index(op.f('ix_team_challenge_public_id'), table_name='team_challenge')
    op.drop_table('team_challenge')
