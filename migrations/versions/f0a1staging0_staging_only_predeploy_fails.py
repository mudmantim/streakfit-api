"""STAGING ONLY: a migration that always fails. NEVER merge, NEVER deploy to production.

Revision ID: f0a1staging0
Revises: e8b3c1f0a7d2
Create Date: 2026-10-13 00:00:00

docs/operations/staging-pw1-test-plan.md, S3 (Q3a): makes Render's
Pre-Deploy (`flask db upgrade`) fail, to observe whether the previous
instance keeps serving. upgrade() raises before any schema change, and
PostgreSQL's transactional DDL leaves the database at e8b3c1f0a7d2.
"""

revision = 'f0a1staging0'
down_revision = 'e8b3c1f0a7d2'
branch_labels = None
depends_on = None


def upgrade():
    raise RuntimeError('staging/predeploy-fails: this migration fails on purpose (staging S3)')


def downgrade():
    pass
