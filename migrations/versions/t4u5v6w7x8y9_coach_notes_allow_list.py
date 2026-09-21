"""Coach Notes become an allow-list of canonical tokens instead of free text.

The old `goals` / `preferences` / `notes` columns held sentences lifted out of
whatever the user typed, matched by broad regex ("remember that ...", "I prefer
...", "my goal is ..."). That is the wrong shape for this problem: any deny-list
sitting in front of it has to anticipate every phrasing of every sensitive
disclosure, and it only has to be wrong once. The replacement stores nothing the
user wrote — only tokens from a closed vocabulary (`COACH_NOTE_TAXONOMY`), so
"walking", "evenings", "no jumping", and nothing else is representable.

Dropping the three columns IS the remediation for data already stored under the
old model: it cannot be inspected and re-classified safely, so it does not
survive. Rows are cleared first so the drop is explicit rather than incidental.
Conversation history (`coach_turn`) is untouched — it is short-lived, rolling,
user-visible, and already cleared by Forget Conversations.

This is one-way by design. `downgrade()` restores the columns but not their
contents, because restoring them would mean restoring the disclosures.

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-09-19
"""
import sqlalchemy as sa
from alembic import op

revision = 't4u5v6w7x8y9'
down_revision = 's3t4u5v6w7x8'
branch_labels = None
depends_on = None


def upgrade():
    # Clear first: every surviving row is re-derived from future conversation
    # under the new rules, and nothing carries over from the old ones.
    op.execute(sa.text('DELETE FROM coach_note'))
    with op.batch_alter_table('coach_note') as batch:
        batch.drop_column('goals')
        batch.drop_column('preferences')
        batch.drop_column('notes')
        batch.add_column(sa.Column('activities', sa.Text(), nullable=False,
                                   server_default='[]'))
        batch.add_column(sa.Column('avoid_movements', sa.Text(), nullable=False,
                                   server_default='[]'))
        batch.add_column(sa.Column('session_prefs', sa.Text(), nullable=False,
                                   server_default='[]'))


def downgrade():
    op.execute(sa.text('DELETE FROM coach_note'))
    with op.batch_alter_table('coach_note') as batch:
        batch.drop_column('activities')
        batch.drop_column('avoid_movements')
        batch.drop_column('session_prefs')
        batch.add_column(sa.Column('goals', sa.Text(), nullable=False,
                                   server_default='[]'))
        batch.add_column(sa.Column('preferences', sa.Text(), nullable=False,
                                   server_default='[]'))
        batch.add_column(sa.Column('notes', sa.Text(), nullable=False,
                                   server_default='[]'))
