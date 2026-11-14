"""index team_message.photo_id  -- PROPOSAL, NOT APPLIED

Revision ID: e5a7c3d91f20
Revises: d4f1b6e2a9c3
Create Date: 2026-11-14 (proposed; lives in migrations/proposals/, which
Alembic does not load, until the owner approves moving it to versions/)

PR #23 round-3 review NEW-2 / round 4. Every read of a photo's chat message
by photo id -- the photo sweep's message write, the caption scrub when a
photo or its sender's account is removed, and PostgreSQL's own foreign-key
check when a team_photo row is DELETED (team deletion) -- is a sequential
scan of team_message today, because team_message.photo_id has no index. Cost
grows with the number of chat messages, inside transactions that hold team
and photo locks. Measurements: STREAKFIT_PHOTOID_INDEX_PROPOSAL.md.

PostgreSQL: CREATE INDEX CONCURRENTLY, which cannot run inside a
transaction. migrations/env.py runs every pending revision in ONE
transaction, so autocommit_block() COMMITS whatever earlier revisions of the
same `flask db upgrade` did, builds the index outside any transaction, then
opens a new transaction for the version stamp and anything after. A
CONCURRENTLY build that fails part-way leaves an INVALID index behind; the
upgrade drops such a leftover first, so re-running is safe.

Adopting it also means declaring the index on the model:
    photo_id = db.Column(db.Integer, db.ForeignKey('team_photo.id'),
                         nullable=True, index=True)
(name ix_team_message_photo_id, the naming Alembic's op.f() gives).

Downgrade drops the index (CONCURRENTLY on PostgreSQL). No code depends on
it; only query plans change.
"""
from alembic import op

revision = 'e5a7c3d91f20'
down_revision = 'd4f1b6e2a9c3'
branch_labels = None
depends_on = None

INDEX = 'ix_team_message_photo_id'


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            invalid = op.get_bind().exec_driver_sql(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = %s AND NOT i.indisvalid", (INDEX,)).first()
            if invalid:
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {INDEX}')
            op.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} '
                       'ON team_message (photo_id)')
    else:
        op.create_index(INDEX, 'team_message', ['photo_id'], unique=False)


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {INDEX}')
    else:
        op.drop_index(INDEX, table_name='team_message')
