"""password change: token version and password_changed_at on user

Revision ID: e8b3c1f0a7d2
Revises: d4f1b6e2a9c3
Create Date: 2026-10-12 15:00:00

PW-1 (docs/architecture/pw1-implementation-plan.md).

- user.token_version INTEGER NOT NULL, server default 0. Access tokens carry
  the version they were issued under; a password change moves it, which
  signs every other device out. Existing rows get 0 from the server default,
  and tokens issued before this release carry no version and read as 0, so
  the deploy signs nobody out.
- user.password_changed_at TIMESTAMP NULL. NULL for every existing account.

Additive only; no row is read or rewritten. On PostgreSQL 11+ a column with a
constant default is a catalogue change, not a table rewrite. Both directions
take a brief ACCESS EXCLUSIVE lock on "user", so both fail fast behind a held
row lock instead of queueing every login behind them.

ROLLBACK ORDER: downgrade FIRST, then redeploy the previous build. That build
runs with STREAKFIT_ENFORCE_DB_HEAD=1 and refuses to boot against a revision
it does not know.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e8b3c1f0a7d2'
down_revision = 'd4f1b6e2a9c3'
branch_labels = None
depends_on = None


def _lock_timeout():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("SET LOCAL lock_timeout = '5s'")


def upgrade():
    _lock_timeout()
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('token_version', sa.Integer(), nullable=False,
                                      server_default='0'))
        batch_op.add_column(sa.Column('password_changed_at', sa.DateTime(), nullable=True))


def downgrade():
    _lock_timeout()
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('password_changed_at')
        batch_op.drop_column('token_version')
