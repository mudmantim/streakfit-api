"""db_guard R7 against a real PostgreSQL: the SERVER's identity decides.

Skipped unless STREAKFIT_TEST_POSTGRES_URL names a DISPOSABLE server. Vanilla
PostgreSQL accepts a custom `neon.endpoint_id` setting through the connection
options, so a local server can be made to CLAIM it is production's Neon
endpoint while the URL says "localhost". That is the case R7 exists for: the
URL names nothing suspicious, the server says otherwise.

Proved end to end, each in a separate process exactly as on Render:
  * `flask db upgrade` as a declared staging deployment refuses before
    migrating -- the database stays empty;
  * the boot check (`import app` with STREAKFIT_ENFORCE_DB_HEAD=1) refuses;
  * the same server claiming a NON-production endpoint migrates and boots.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')
pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')

REPO = Path(__file__).resolve().parent.parent
PROD_EP = 'ep-odd-star-ajye3lag'
OTHER_EP = 'ep-fake-staging-00000000'


@pytest.fixture
def scratch():
    server = make_url(PG_URL)
    admin = create_engine(server.set(database='postgres'), isolation_level='AUTOCOMMIT')
    name = f'sf_ident_{uuid.uuid4().hex[:8]}'
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield server.set(database=name)
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def claiming(url, endpoint):
    """The same server, but every session reports `endpoint` as neon.endpoint_id."""
    return url.update_query_dict({'options': f'-c neon.endpoint_id={endpoint}'}).render_as_string(
        hide_password=False)


def run(code_or_args, db_url, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ('RENDER', 'RENDER_SERVICE_ID', 'STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER',
                        'MIGRATION_DATABASE_URL', 'STREAKFIT_DEPLOYMENT')}
    env.update(DATABASE_URL=db_url, FLASK_APP='app', SECRET_KEY='ident-test',
               JWT_SECRET_KEY='ident-test-jwt-0123456789abcdef0123456789', PYTHONDONTWRITEBYTECODE='1', **extra)
    args = [sys.executable, '-m', 'flask', 'db', 'upgrade'] if code_or_args == 'upgrade' else \
        [sys.executable, '-c', code_or_args]
    return subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True, timeout=600)


def tables(url):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(
                "select count(*) from information_schema.tables where table_schema = 'public'")).scalar()
    finally:
        eng.dispose()


def test_the_option_really_reaches_the_server(scratch):
    eng = create_engine(claiming(scratch, PROD_EP))
    try:
        with eng.connect() as c:
            assert c.execute(text("select current_setting('neon.endpoint_id', true)")).scalar() == PROD_EP
    finally:
        eng.dispose()


def test_staging_migration_refuses_a_server_claiming_production(scratch):
    p = run('upgrade', claiming(scratch, PROD_EP), STREAKFIT_DEPLOYMENT='staging')
    assert p.returncode != 0
    assert 'R3' in p.stdout + p.stderr and PROD_EP in p.stdout + p.stderr
    assert tables(scratch) == 0, 'migrations ran against a server that said it was production'


def test_staging_boot_refuses_a_server_claiming_production(scratch):
    assert run('upgrade', claiming(scratch, OTHER_EP), STREAKFIT_DEPLOYMENT='staging').returncode == 0
    p = run('import app', claiming(scratch, PROD_EP), STREAKFIT_DEPLOYMENT='staging', STREAKFIT_ENFORCE_DB_HEAD='1')
    assert p.returncode != 0
    assert 'Refusing to start: R3' in p.stderr


def test_a_laptop_refuses_a_server_claiming_production_without_the_override(scratch):
    p = run('upgrade', claiming(scratch, PROD_EP))
    assert p.returncode != 0 and 'R5' in p.stdout + p.stderr
    assert tables(scratch) == 0


def test_a_server_claiming_a_non_production_endpoint_migrates_and_boots(scratch):
    url = claiming(scratch, OTHER_EP)
    up = run('upgrade', url, STREAKFIT_DEPLOYMENT='staging')
    assert up.returncode == 0, up.stderr[-2000:]
    assert 'server endpoint ' + OTHER_EP in up.stderr
    boot = run('import app; print("BOOTED", app._DB_IDENTITY_VERIFIED)', url, STREAKFIT_DEPLOYMENT='staging',
               STREAKFIT_ENFORCE_DB_HEAD='1')
    assert boot.returncode == 0, boot.stderr[-2000:]
    assert 'BOOTED True' in boot.stdout
    assert tables(scratch) > 30
