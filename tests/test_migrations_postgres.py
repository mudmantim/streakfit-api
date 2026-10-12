"""Migration-chain integrity on PostgreSQL -- the database production runs.

tests/test_migrations.py proves the chain builds from empty and matches the
models, but only on SQLite. Production is PostgreSQL, where types, indexes and
constraints are introspected differently, and a migration can use
dialect-specific branches (several do: `lock_timeout`, partial behaviour on
`postgresql`). This runs the same comparison, with the same facets and the same
deliberate exclusion of server defaults, against two scratch PostgreSQL
databases: one built by `flask db upgrade`, one by `create_all()`.

Needs a server URL (any database on it); it creates and drops its own scratch
databases, so it never touches whatever STREAKFIT_TEST_POSTGRES_URL names.
"""
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.test_migrations import _introspect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')
pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')


@pytest.fixture
def scratch_dbs():
    server = make_url(PG_URL)
    admin = create_engine(server.set(database='postgres'), isolation_level='AUTOCOMMIT')
    names = [f'sf_mig_{kind}_{uuid.uuid4().hex[:8]}' for kind in ('chain', 'models')]
    with admin.connect() as c:
        for name in names:
            c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield [server.set(database=n).render_as_string(hide_password=False) for n in names]
    finally:
        with admin.connect() as c:
            for name in names:
                c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_migration_chain_builds_from_empty_and_matches_models_on_postgres(scratch_dbs):
    chain_url, models_url = scratch_dbs
    env = {**os.environ, 'DATABASE_URL': chain_url, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    result = subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'],
                            cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]

    from app import db
    engine = create_engine(models_url)
    try:
        db.metadata.create_all(engine)
    finally:
        engine.dispose()

    migrated = _introspect(chain_url)
    model = _introspect(models_url)
    assert set(migrated) == set(model), (
        f"migrated-only={set(migrated) - set(model)}, model-only={set(model) - set(migrated)}")
    mismatches = [
        f"  {table}.{facet}:\n    from migrations = {migrated[table][facet]}\n"
        f"    from models     = {model[table][facet]}"
        for table in sorted(migrated)
        for facet in ('columns', 'pk', 'fks', 'unique', 'indexes')
        if migrated[table][facet] != model[table][facet]]
    assert not mismatches, (
        "On PostgreSQL, the schema built from the migration chain does not match "
        "the models:\n" + "\n".join(mismatches))
