"""daily mission: the day's five exercises and Brain Boost, decided once

Revision ID: d4f1b6e2a9c3
Revises: c7d2a9e41b30
Create Date: 2026-10-11 23:00:00

Step 1 of the D6/D50/D59 day-boundary work (D59/D68 mission identity).

- daily_mission (user_id, mission_date) primary key: one stored mission per
  person per day -- five category slots and the Brain Boost question as
  presented. The same (user, day) identity as daily_mission_award.

Additive only. No existing row is read, changed or backfilled: past days keep
exactly the DailyCompletion / DailyMissionAward / BrainBoostAnswer rows they
have, and a day already in progress at deploy gets its mission row on its next
request, adopting the completions it already has.

Downgrade drops the table; the previous code never reads it.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4f1b6e2a9c3'
down_revision = 'c7d2a9e41b30'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        # The foreign key takes a lock on "user" while the table is created.
        # Behind a held row lock it would wait without limit; fail fast and let
        # the deploy stop instead (same reasoning as c7d2a9e41b30).
        op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        'daily_mission',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('mission_date', sa.Date(), nullable=False),
        sa.Column('skill_level', sa.String(length=16), nullable=False),
        sa.Column('effort', sa.String(length=16), nullable=False),
        sa.Column('next_effort', sa.String(length=16), nullable=True),
        sa.Column('slots', sa.JSON(), nullable=False),
        sa.Column('brain_boost', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('generator_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('user_id', 'mission_date'),
    )


def downgrade():
    op.drop_table('daily_mission')
