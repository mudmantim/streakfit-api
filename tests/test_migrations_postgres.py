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


# ── a1c4e0f2b7d9: a team can be ownerless (owner decision #1, round 6) ──────

def _flask_db(url, *args, timeout=300):
    env = {**os.environ, 'DATABASE_URL': url, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    return subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=timeout)


def _owner_nullable(engine):
    with engine.connect() as c:
        return c.execute(text(
            "select is_nullable from information_schema.columns "
            "where table_name = 'team' and column_name = 'created_by_user_id'")).scalar()


def _version(engine):
    with engine.connect() as c:
        return c.execute(text('select version_num from alembic_version')).scalar()


def test_m1_round_trips_on_postgres_and_refuses_while_a_team_is_ownerless(scratch_dbs):
    url = scratch_dbs[0]
    assert _flask_db(url, 'upgrade', 'd4f1b6e2a9c3').returncode == 0
    engine = create_engine(url)
    try:
        assert _owner_nullable(engine) == 'NO'
        with engine.begin() as c:
            uid = c.execute(text("insert into \"user\" (username, password_hash, skill_level, "
                                 "display_mode, rickie_mode, xp_total, acorns_total, acorns_spent, "
                                 "is_plus) values ('m1', 'x', 'beginner', 'game', 'full', 0, 0, 0, "
                                 "false) returning id")).scalar()
            c.execute(text("insert into team (name, created_by_user_id, created_at) "
                           "values ('kept', :u, now())"), {'u': uid})
        up = _flask_db(url, 'upgrade')
        assert up.returncode == 0, up.stderr[-2000:]
        assert _version(engine) == 'a1c4e0f2b7d9' and _owner_nullable(engine) == 'YES'
        # No backfill: the existing team keeps its owner.
        with engine.connect() as c:
            assert c.execute(text('select count(*) from team where created_by_user_id is null')
                             ).scalar() == 0
        with engine.begin() as c:
            c.execute(text('update team set created_by_user_id = null'))
        down = _flask_db(url, 'downgrade', 'd4f1b6e2a9c3')
        assert down.returncode != 0 and '1 team(s) are ownerless' in down.stderr
        assert _version(engine) == 'a1c4e0f2b7d9' and _owner_nullable(engine) == 'YES'
        with engine.begin() as c:
            c.execute(text('update team set created_by_user_id = :u'), {'u': uid})
        down = _flask_db(url, 'downgrade', 'd4f1b6e2a9c3')
        assert down.returncode == 0, down.stderr[-2000:]
        assert _version(engine) == 'd4f1b6e2a9c3' and _owner_nullable(engine) == 'NO'
        assert _flask_db(url, 'upgrade').returncode == 0
        assert _owner_nullable(engine) == 'YES'
    finally:
        engine.dispose()


def test_m1_fails_fast_behind_a_held_team_lock_and_changes_nothing(scratch_dbs):
    """DROP NOT NULL needs ACCESS EXCLUSIVE on team. Behind a held row lock it
    must stop on its 5 s lock_timeout, not queue every team query behind it."""
    import time
    url = scratch_dbs[0]
    assert _flask_db(url, 'upgrade', 'd4f1b6e2a9c3').returncode == 0
    engine = create_engine(url)
    holder = engine.connect()
    try:
        with engine.begin() as c:
            uid = c.execute(text("insert into \"user\" (username, password_hash, skill_level, "
                                 "display_mode, rickie_mode, xp_total, acorns_total, acorns_spent, "
                                 "is_plus) values ('m1l', 'x', 'beginner', 'game', 'full', 0, 0, 0, "
                                 "false) returning id")).scalar()
            c.execute(text("insert into team (name, created_by_user_id, created_at) "
                           "values ('busy', :u, now())"), {'u': uid})
        tx = holder.begin()
        holder.execute(text('select id from team for update'))
        started = time.monotonic()
        up = _flask_db(url, 'upgrade', timeout=120)
        took = time.monotonic() - started
        assert up.returncode != 0 and 'lock timeout' in up.stderr, up.stderr[-1500:]
        assert took < 60, took
        tx.rollback()
        assert _version(engine) == 'd4f1b6e2a9c3' and _owner_nullable(engine) == 'NO'
        assert _flask_db(url, 'upgrade').returncode == 0           # and then it simply works
        assert _owner_nullable(engine) == 'YES'
    finally:
        holder.close()
        engine.dispose()
