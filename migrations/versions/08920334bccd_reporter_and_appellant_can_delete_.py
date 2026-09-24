"""a reporter or appellant can delete their account; the record stays

Revision ID: 08920334bccd
Revises: 47f7dc9962e3
Create Date: 2026-09-24 10:30:00

report.reporter_user_id and appeal.user_id become nullable. Account deletion
sets them to NULL, so a report or an appeal survives as the minimal audit
record (moderation policy decision 6: that it existed, its category, its dates,
its outcome) without saying who filed it.

Also adds team_challenge.target_left_at (nullable): set when the person a
challenge named deletes their account, so history does not read their cut
link as "the whole team". No existing data is changed by the upgrade.

The downgrade refuses once any row has actually lost its person: restoring NOT
NULL would need a user id that no longer exists, and inventing one would be a
false record.
"""
from alembic import op
import sqlalchemy as sa


revision = '08920334bccd'
down_revision = '47f7dc9962e3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('team_challenge', schema=None) as batch_op:
        batch_op.add_column(sa.Column('target_left_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('report', schema=None) as batch_op:
        batch_op.alter_column('reporter_user_id', existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table('appeal', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    bind = op.get_bind()
    orphaned = bind.execute(sa.text(
        "select (select count(*) from report where reporter_user_id is null)"
        " + (select count(*) from appeal where user_id is null)")).scalar()
    if orphaned:
        raise RuntimeError(
            f"{orphaned} report/appeal row(s) belong to deleted accounts; they "
            "cannot be made NOT NULL again. Roll forward instead.")
    with op.batch_alter_table('appeal', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table('report', schema=None) as batch_op:
        batch_op.alter_column('reporter_user_id', existing_type=sa.Integer(), nullable=False)
    # Dropping it loses only the "former teammate" wording on old cards, which
    # the old code could not show anyway.
    with op.batch_alter_table('team_challenge', schema=None) as batch_op:
        batch_op.drop_column('target_left_at')
