"""baseline

Revision ID: a3f8b1c2d4e5
Revises:
Create Date: 2026-06-04 00:00:00.000000

Backfilled 2026-07-23: this baseline originally shipped as a no-op (pass/pass)
because, at the time it was stamped, the `user` and `challenge` tables already
existed in the live database (they predate Alembic in this project). That made
the whole migration chain impossible to run against an *empty* database: the
next revision immediately does `add_column('user', ...)`, but nothing had ever
created `user` (or `challenge`, which no migration touches at all).

This baseline now creates those two pre-Alembic tables at their *baseline-era*
schema — `user` with only its three original columns (the six later columns are
added by subsequent migrations: skill_level, display_mode, xp_total,
acorns_total, rickie_mode, is_plus) and `challenge` in full. The rest of the
chain builds everything else. Net result: `flask db upgrade` now produces the
complete, correct schema from an empty database with no manual bootstrap.

Safe for existing databases: production is stamped at head, so Alembic never
re-runs this baseline there — this change only ever executes on a fresh DB.
Proven by tests/test_migrations.py (from-empty build == model schema).
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3f8b1c2d4e5'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # `user` — baseline-era columns only. skill_level/display_mode/xp_total/
    # acorns_total/rickie_mode/is_plus are added by later revisions and must NOT
    # appear here, or those add_column migrations would collide.
    op.create_table(
        'user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
    )

    # `challenge` (Side Quests) — never created or altered anywhere else in the
    # chain, so its full current schema lives here.
    op.create_table(
        'challenge',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('current_streak', sa.Integer(), nullable=True),
        sa.Column('longest_streak', sa.Integer(), nullable=True),
        sa.Column('last_check_in', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    # Reverse order for the FK. Only ever runs on a full teardown to base.
    op.drop_table('challenge')
    op.drop_table('user')
