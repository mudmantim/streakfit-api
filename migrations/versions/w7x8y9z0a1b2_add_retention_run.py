"""Add retention_run — evidence that the conversation sweep actually happened.

"Conversations are deleted after 30 days" was unfalsifiable from inside the
product. A scheduled sweep that has silently stopped running looks exactly like
one that ran and found nothing expired: both print nothing, both leave the
database unchanged. On a quiet week you cannot tell them apart, and the week you
most need to tell them apart is the quiet one.

One short append-only row per sweep, written even when nothing was deleted,
because "it ran and there was nothing to do" is the answer that matters most
often. `/api/verification/self` reads the newest row and fails if it is older
than 48 hours.

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
"""
import sqlalchemy as sa
from alembic import op

revision = 'w7x8y9z0a1b2'
down_revision = 'v6w7x8y9z0a1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'retention_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ran_at', sa.DateTime(), nullable=False),
        sa.Column('deleted', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=24), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('retention_run', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_retention_run_ran_at'),
                              ['ran_at'], unique=False)


def downgrade():
    with op.batch_alter_table('retention_run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_retention_run_ran_at'))
    op.drop_table('retention_run')
