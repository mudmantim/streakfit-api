"""docs/operations/db-roles/*.sql against a real PostgreSQL (Phase B-2 kit).

Skipped unless STREAKFIT_TEST_POSTGRES_URL names a DISPOSABLE server. Creates
one scratch database there, builds the schema with the real migration chain
as an owner role named `neondb_owner` (as on Neon), applies 01, and proves --
by connecting AS streakfit_app, not by reading catalogs -- that the role can do
what the app needs and nothing else. Then 02 must return zero rows, a
deliberately broken grant must make 02 return a row, and 99 must remove the
role cleanly. Drops the scratch database and roles afterwards.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')
pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / 'docs' / 'operations' / 'db-roles'
APP_PW = 'kit-test-app-' + uuid.uuid4().hex
OWNER_PW = 'kit-test-owner-' + uuid.uuid4().hex


def run_literal(engine, body):
    """Execute a kit file exactly as psql / the Neon SQL editor would: no
    parameter substitution (the kit uses format('%I'))."""
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True
        with raw.driver_connection.cursor() as cur:
            cur.execute(body)
    finally:
        raw.close()


def sql_file(name, **subst):
    body = (KIT / name).read_text()
    for k, v in subst.items():
        body = body.replace(k, v)
    return body


@pytest.fixture(scope='module')
def scratch():
    server = make_url(PG_URL)
    admin = create_engine(server.set(database='postgres'), isolation_level='AUTOCOMMIT')
    db = f'sf_roles_{uuid.uuid4().hex[:8]}'
    with admin.connect() as c:
        for role in ('streakfit_app', 'neondb_owner'):
            c.execute(text(f'drop role if exists {role}'))
        c.execute(text(f"create role neondb_owner login createrole password '{OWNER_PW}'"))
        c.execute(text(f'create database "{db}" owner neondb_owner'))
    owner_url = server.set(database=db, username='neondb_owner', password=OWNER_PW)
    with create_engine(server.set(database=db), isolation_level='AUTOCOMMIT').connect() as c:
        # PG15+ default: public owned by pg_database_owner, PUBLIC has USAGE only -- as on Neon.
        c.execute(text('alter schema public owner to pg_database_owner'))
    env = {**os.environ, 'DATABASE_URL': owner_url.render_as_string(hide_password=False), 'FLASK_APP': 'app',
           'SECRET_KEY': 'kit', 'JWT_SECRET_KEY': 'kit-jwt-0123456789abcdef0123456789'}
    for k in ('STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER', 'RENDER', 'RENDER_SERVICE_ID',
              'MIGRATION_DATABASE_URL', 'STREAKFIT_DEPLOYMENT'):
        env.pop(k, None)
    p = subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr[-3000:]
    owner = create_engine(owner_url, isolation_level='AUTOCOMMIT')
    run_literal(owner, sql_file('01_create_app_role.sql', **{'<APP_PASSWORD>': APP_PW}))
    app_engine = create_engine(server.set(database=db, username='streakfit_app', password=APP_PW))
    yield owner, app_engine
    app_engine.dispose()
    owner.dispose()
    with admin.connect() as c:
        c.execute(text(f'drop database if exists "{db}" with (force)'))
        for role in ('streakfit_app', 'neondb_owner'):
            c.execute(text(f'drop role if exists {role}'))
    admin.dispose()


def defects(owner):
    body = sql_file('02_verify_app_role.sql')
    select = body.split('begin read only;', 1)[1].rsplit('rollback;', 1)[0]
    with owner.connect() as c:
        return [tuple(r) for r in c.execute(text(select))]


def test_the_app_role_can_do_what_the_app_does(scratch):
    _, app_engine = scratch
    with app_engine.begin() as c:
        assert c.execute(text('select version_num from alembic_version')).scalar()
        c.execute(text("insert into analytics_event (event_name, created_at) values ('kit_test', now())"))
        c.execute(text("update analytics_event set event_name = 'kit_test2' where event_name = 'kit_test'"))
        c.execute(text("select * from analytics_event where event_name = 'kit_test2' for update skip locked"))
        c.execute(text("delete from analytics_event where event_name = 'kit_test2'"))


@pytest.mark.parametrize('statement', [
    'create table kit_should_fail (id int)',
    'alter table analytics_event add column kit_should_fail int',
    'drop table analytics_event',
    'truncate analytics_event',
    "update alembic_version set version_num = 'x'",
    'delete from alembic_version',
    'create role kit_should_fail',
    'create index kit_should_fail on analytics_event (event_name)',
])
def test_the_app_role_cannot_change_the_schema_or_the_migration_record(scratch, statement):
    _, app_engine = scratch
    with app_engine.connect() as c, pytest.raises(ProgrammingError) as exc:
        c.execute(text(statement))
    assert 'permission denied' in str(exc.value).lower() or 'must be owner' in str(exc.value).lower()


def test_verify_returns_zero_rows_when_correct(scratch):
    owner, _ = scratch
    assert defects(owner) == []


def test_tables_created_by_future_migrations_are_usable_by_the_app(scratch):
    owner, app_engine = scratch
    with owner.connect() as c:
        c.execute(text('create table kit_future (id serial primary key, v text)'))
    try:
        with app_engine.begin() as c:
            c.execute(text("insert into kit_future (v) values ('ok')"))
        assert defects(owner) == []
    finally:
        with owner.connect() as c:
            c.execute(text('drop table kit_future'))


def test_verify_reports_a_broken_grant(scratch):
    owner, _ = scratch
    with owner.connect() as c:
        c.execute(text('revoke delete on analytics_event from streakfit_app'))
        c.execute(text('grant create on schema public to streakfit_app'))
    try:
        found = defects(owner)
        assert ('missing DELETE on table', 'analytics_event') in found
        assert ('may CREATE in schema public', None) in found
    finally:
        with owner.connect() as c:
            c.execute(text('grant delete on analytics_event to streakfit_app'))
            c.execute(text('revoke create on schema public from streakfit_app'))
    assert defects(owner) == []


def test_zz_rollback_removes_the_role_cleanly(scratch):
    owner, app_engine = scratch
    app_engine.dispose()
    run_literal(owner, sql_file('99_rollback_app_role.sql'))
    with owner.connect() as c:
        assert c.execute(text("select count(*) from pg_roles where rolname = 'streakfit_app'")).scalar() == 0
        assert c.execute(text("select count(*) from pg_default_acl")).scalar() == 0
