"""Add user.display_name — what Rickie calls somebody, separate from their login.

Rickie was handed `user.username` as the user's name and said it back in
conversation. A username is a login credential: people fill them with email
addresses and machine-generated handles, and the September evaluation produced
"Be a little gentle with yourself right now, qa_coach_eval_1789836556_2".

Nullable, with no backfill on purpose. An empty display_name means "no name I
can safely use", and `_safe_display_name()` then falls back to the username only
when it already looks like something a person would answer to. Backfilling
username into display_name would have copied the exact values this exists to
avoid saying out loud.

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
"""
import sqlalchemy as sa
from alembic import op

revision = 'v6w7x8y9z0a1'
down_revision = 'u5v6w7x8y9z0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('display_name', sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('display_name')
