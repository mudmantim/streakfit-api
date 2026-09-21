"""How hard a person asked today to be.

Replaces an automatic escalation that raised difficulty once someone had
finished fourteen missions. Mission count measures consistency and says nothing
about capability — a completion records only a user, a date and an exercise
key, so the data needed to establish physical readiness is not in this schema
and no amount of arithmetic on what is there can produce it. The honest source
of "I could do a bit more today" is the person, which is what this table holds.

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-09-19
"""
import sqlalchemy as sa
from alembic import op

revision = 'u5v6w7x8y9z0'
down_revision = 't4u5v6w7x8y9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'daily_effort',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', name='uq_daily_effort'),
    )


def downgrade():
    op.drop_table('daily_effort')
