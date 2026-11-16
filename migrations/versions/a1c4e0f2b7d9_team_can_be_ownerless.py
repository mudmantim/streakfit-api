"""a team can be explicitly ownerless (team.created_by_user_id nullable)

Revision ID: a1c4e0f2b7d9
Revises: d4f1b6e2a9c3
Create Date: 2026-11-15

Owner decision #1 (Tim, 2026-11-14/15: P11 "M1 only", P1, P3, P4, P4-safety
(b), P7, P9). Ownerless == team.created_by_user_id IS NULL. The app writes
NULL only when:

- the owner leaves and chooses "nobody" (P3);
- the account-deletion fallback finds no verified adult to hand a team to (P1);
- an owner deletes their account while a report or legal hold keeps their team
  (P9: the team is kept, ownerless, and its evidence stays);
- a sole owner leaves a team that a report or hold keeps (so the empty-team
  cleanup can delete it once the review ends).

An ownerless team is read-only (P4-safety (b)); see app.py
`_team_is_read_only`.

NO BACKFILL. Every existing row keeps its owner.

PostgreSQL: DROP NOT NULL is catalog-only (no rewrite, no scan) but needs
ACCESS EXCLUSIVE on "team". Behind a held team row lock (account deletion's
FOR UPDATE, a join's FOR NO KEY UPDATE) it would queue, and every team query
would queue behind it, so it fails fast instead (as d4f1b6e2a9c3).

Downgrade refuses while any team is ownerless: restoring NOT NULL would need
an owner id nobody chose. Roll forward instead (precedent: 08920334bccd).
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1c4e0f2b7d9'
down_revision = 'd4f1b6e2a9c3'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("SET LOCAL lock_timeout = '5s'")
    with op.batch_alter_table('team', schema=None) as batch_op:
        batch_op.alter_column('created_by_user_id', existing_type=sa.Integer(),
                              nullable=True)


def downgrade():
    bind = op.get_bind()
    ownerless = bind.execute(sa.text(
        "SELECT count(*) FROM team WHERE created_by_user_id IS NULL")).scalar()
    if ownerless:
        raise RuntimeError(
            f"{ownerless} team(s) are ownerless; team.created_by_user_id cannot "
            "be made NOT NULL again without inventing an owner nobody chose. "
            "Roll forward instead (deploy code that understands ownerless teams).")
    if bind.dialect.name == 'postgresql':
        # SET NOT NULL scans the table under ACCESS EXCLUSIVE; bounded wait.
        op.execute("SET LOCAL lock_timeout = '5s'")
    with op.batch_alter_table('team', schema=None) as batch_op:
        batch_op.alter_column('created_by_user_id', existing_type=sa.Integer(),
                              nullable=False)
