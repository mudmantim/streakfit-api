"""Account deletion on PostgreSQL, the engine production runs.

Opt-in: set STREAKFIT_TEST_POSTGRES_URL to an EMPTY, disposable database. The
test builds it with `flask db upgrade` -- the migration chain, not create_all --
so the foreign keys checked are the ones production actually has, then runs
tests/account_deletion_scenario.py against it in a subprocess (the app module
reads DATABASE_URL once at import, and this suite's copy is bound to SQLite).

Skipped without the variable, so the default suite needs no database server.
"""
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

import app as appmod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')

pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')


@pytest.fixture(scope='module')
def migrated_pg():
    engine = create_engine(PG_URL)
    with engine.connect() as conn:
        tables = conn.execute(text(
            "select count(*) from information_schema.tables "
            "where table_schema = 'public'")).scalar()
    assert tables == 0, 'STREAKFIT_TEST_POSTGRES_URL must point at an empty database'
    env = {**os.environ, 'DATABASE_URL': PG_URL, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    result = subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'],
                            cwd=REPO, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:]
    yield engine, env
    engine.dispose()


def test_every_postgres_foreign_key_into_user_has_a_deletion_rule(migrated_pg):
    engine, _env = migrated_pg
    with engine.connect() as conn:
        rows = conn.execute(text("""
            select c.conrelid::regclass::text, a.attname, c.confdeltype
            from pg_constraint c
            join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
            where c.contype = 'f' and c.confrelid = '"user"'::regclass
        """)).all()
    live = sorted((t.strip('"'), col) for t, col, _rule in rows)
    rules = appmod._user_fk_classification()
    assert live == sorted(rules), (
        f"no rule: {sorted(set(live) - set(rules))}; "
        f"rule for a key production does not have: {sorted(set(rules) - set(live))}")
    # Every one is NO ACTION ('a'): the application, not the database, decides.
    assert {rule for _t, _c, rule in rows} == {'a'}


def test_deletion_scenario_on_postgres(migrated_pg):
    _engine, env = migrated_pg
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'account_deletion_scenario.py')],
        cwd=REPO, env={**env, 'PYTHONPATH': REPO}, capture_output=True, text=True)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the scenario\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v['detail'] for k, v in out.items() if not v['ok']}
    assert not failed and result.returncode == 0, failed
