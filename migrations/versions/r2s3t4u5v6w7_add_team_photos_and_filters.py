"""Add team photo sharing and StreakFit photo filters.

Three things arrive together because they only make sense together:

  * `team_photo` holds the image bytes. They live in the database because
    Render's web filesystem is wiped on every deploy, so disk is not an option,
    and object storage would be a new paid service. The client composites and
    resizes before upload (~200 KB a row) and rows expire, so the working set
    stays small. See docs/team-photos.md for the migration path off Postgres.
  * `team_message.photo_id` hangs a photo off the existing thread instead of
    building a second feed, so ordering and the emoji reactions already there
    keep working with no new schema.
  * `user_filter_unlock` + `user.acorns_spent` give acorns their first sink.
    `acorns_total` stays lifetime-EARNED (the acorns_100 milestone depends on
    that), so a balance is earned minus spent.

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-09-18
"""
import sqlalchemy as sa
from alembic import op

revision = 'r2s3t4u5v6w7'
down_revision = 'q1r2s3t4u5v6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'team_photo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('sender_user_id', sa.Integer(), nullable=False),
        sa.Column('caption', sa.String(length=140), nullable=True),
        sa.Column('filter_key', sa.String(length=40), nullable=True),
        sa.Column('image_data', sa.LargeBinary(), nullable=True),
        sa.Column('content_type', sa.String(length=32), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['team.id']),
        sa.ForeignKeyConstraint(['sender_user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        # Uniqueness comes from the unique INDEX below, not a separate named
        # constraint -- the model declares unique=True alongside index=True,
        # which SQLAlchemy renders as exactly one unique index. Declaring both
        # here makes the schema drift from the models, which
        # tests/test_migrations.py fails on (and did).
    )
    op.create_index(op.f('ix_team_photo_public_id'), 'team_photo', ['public_id'], unique=True)
    op.create_index(op.f('ix_team_photo_team_id'), 'team_photo', ['team_id'], unique=False)
    op.create_index(op.f('ix_team_photo_created_at'), 'team_photo', ['created_at'], unique=False)

    op.create_table(
        'user_filter_unlock',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filter_key', sa.String(length=40), nullable=False),
        sa.Column('acorns_spent', sa.Integer(), nullable=False),
        sa.Column('unlocked_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'filter_key', name='uq_user_filter_unlock'),
    )
    op.create_index(op.f('ix_user_filter_unlock_user_id'), 'user_filter_unlock', ['user_id'], unique=False)

    # server_default so the column is non-null for rows that already exist;
    # the model's Python-side default takes over for new rows.
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('acorns_spent', sa.Integer(), nullable=False, server_default='0'))

    with op.batch_alter_table('team_message') as batch:
        batch.add_column(sa.Column('photo_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_team_message_photo', 'team_photo', ['photo_id'], ['id'])


def downgrade():
    with op.batch_alter_table('team_message') as batch:
        batch.drop_constraint('fk_team_message_photo', type_='foreignkey')
        batch.drop_column('photo_id')

    with op.batch_alter_table('user') as batch:
        batch.drop_column('acorns_spent')

    op.drop_index(op.f('ix_user_filter_unlock_user_id'), table_name='user_filter_unlock')
    op.drop_table('user_filter_unlock')

    op.drop_index(op.f('ix_team_photo_created_at'), table_name='team_photo')
    op.drop_index(op.f('ix_team_photo_team_id'), table_name='team_photo')
    op.drop_index(op.f('ix_team_photo_public_id'), table_name='team_photo')
    op.drop_table('team_photo')
