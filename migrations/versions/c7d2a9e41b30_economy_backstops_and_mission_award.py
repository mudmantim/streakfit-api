"""economy backstops: the mission award row and the user balance checks

Revision ID: c7d2a9e41b30
Revises: 08920334bccd
Create Date: 2026-10-03 22:00:00

RC-B2a. The request paths now take a per-user row lock and commit once; these
are the database's own guarantees underneath that, so a future path that
forgets the lock still cannot store an impossible state.

- daily_mission_award (user_id, mission_date) primary key: the mission and
  perfect bonuses are paid by inserting this row, so a second payment for the
  same person and day is impossible (D27b).
- user CHECK constraints: xp_total >= 0, acorns_total >= 0, and
  0 <= acorns_spent <= acorns_total (D26).

No existing data is changed. The constraints are validated against existing
rows when they are added: if production held an impossible balance the
upgrade would FAIL at pre-deploy (and the deploy would stop) rather than
silently accept it -- run the read-only precondition query before deploying.
No backfill of daily_mission_award: the bonus only ever fires on today's
fifth new completion, so past days need no row.

Downgrade drops the table and the constraints; nothing is lost that the
previous code reads.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d2a9e41b30'
down_revision = '08920334bccd'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'daily_mission_award',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('mission_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('user_id', 'mission_date'),
    )
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.create_check_constraint('ck_user_xp_nonnegative', 'xp_total >= 0')
        batch_op.create_check_constraint('ck_user_acorns_nonnegative', 'acorns_total >= 0')
        batch_op.create_check_constraint(
            'ck_user_acorns_spent_within_earned',
            'acorns_spent >= 0 AND acorns_spent <= acorns_total')


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint('ck_user_acorns_spent_within_earned', type_='check')
        batch_op.drop_constraint('ck_user_acorns_nonnegative', type_='check')
        batch_op.drop_constraint('ck_user_xp_nonnegative', type_='check')
    op.drop_table('daily_mission_award')
