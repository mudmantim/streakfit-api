"""Step 1 on PostgreSQL: the daily_mission migration and the races around it.

Opt-in, like test_concurrency_postgres: STREAKFIT_TEST_POSTGRES_URL names a
disposable server. This module never touches that database itself -- it
creates its own throwaway database next to it (so it can run in the same
session as the other PostgreSQL tests, which require theirs to be empty), and
drops it afterwards.
"""
import json
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')
pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')

PREVIOUS_HEAD = 'c7d2a9e41b30'


@pytest.fixture(scope='module')
def scratch_db():
    server = make_url(PG_URL)
    name = f'sf_step1_{uuid.uuid4().hex[:10]}'
    admin = create_engine(server.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = server.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _flask(url, *args):
    env = {**os.environ, 'DATABASE_URL': url, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test', 'PYTHONPATH': REPO}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    return subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=300)


def _shape(url, table):
    eng = create_engine(url)
    try:
        ins = inspect(eng)
        if table not in ins.get_table_names():
            return None
        return {
            'columns': sorted((c['name'], str(c['type']), c['nullable']) for c in ins.get_columns(table)),
            'pk': sorted(ins.get_pk_constraint(table)['constrained_columns']),
            'fks': sorted((tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns']))
                          for f in ins.get_foreign_keys(table)),
            'tables': sorted(ins.get_table_names()),
        }
    finally:
        eng.dispose()


def test_migration_builds_downgrades_and_rebuilds_on_postgres(scratch_db):
    up = _flask(scratch_db, 'upgrade')
    assert up.returncode == 0, up.stderr[-3000:]
    built = _shape(scratch_db, 'daily_mission')
    assert built['pk'] == ['mission_date', 'user_id']
    assert built['fks'] == [(('user_id',), 'user', ('id',))]
    cols = {c[0]: c for c in built['columns']}
    assert cols['slots'][1] == 'JSON' and cols['brain_boost'][1] == 'JSON'
    assert cols['next_effort'][2] is True and cols['updated_at'][2] is True
    assert all(c[2] is False for n, c in cols.items() if n not in ('next_effort', 'updated_at'))

    down = _flask(scratch_db, 'downgrade', PREVIOUS_HEAD)
    assert down.returncode == 0, down.stderr[-3000:]
    after_down = _shape(scratch_db, 'user')
    assert 'daily_mission' not in after_down['tables']
    assert set(built['tables']) - set(after_down['tables']) == {'daily_mission'}

    again = _flask(scratch_db, 'upgrade')
    assert again.returncode == 0, again.stderr[-3000:]
    assert _shape(scratch_db, 'daily_mission') == built


def test_model_and_migration_agree_on_postgres(scratch_db):
    """The SQLite structural comparison lives in test_migrations; this checks
    the new table's PostgreSQL types against the model's own DDL."""
    code = (
        "import os, json; os.environ['DATABASE_URL']=%r\n"
        "from sqlalchemy.schema import CreateTable\n"
        "from sqlalchemy.dialects import postgresql\n"
        "import app as A\n"
        "print(json.dumps(str(CreateTable(A.DailyMission.__table__).compile(dialect=postgresql.dialect()))))\n"
    ) % scratch_db
    out = subprocess.run([sys.executable, '-c', code], cwd=REPO, capture_output=True, text=True,
                         env={**os.environ, 'SECRET_KEY': 't', 'JWT_SECRET_KEY': 't', 'PYTHONPATH': REPO})
    assert out.returncode == 0, out.stderr[-2000:]
    ddl = json.loads(out.stdout.strip().splitlines()[-1])
    for col, typ in (('slots', 'JSON'), ('brain_boost', 'JSON'), ('mission_date', 'DATE'),
                     ('skill_level', 'VARCHAR(16)'), ('created_at', 'TIMESTAMP WITHOUT TIME ZONE')):
        assert f'{col} {typ}' in ddl, (col, ddl)


@pytest.fixture(scope='module')
def race_results(scratch_db):
    up = _flask(scratch_db, 'upgrade')
    assert up.returncode == 0, up.stderr[-3000:]
    env = {**os.environ, 'DATABASE_URL': scratch_db, 'SECRET_KEY': 'test',
           'JWT_SECRET_KEY': 'test', 'PYTHONPATH': REPO}
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    run = subprocess.run([sys.executable, os.path.join('tests', 'daily_mission_race.py')],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    try:
        results = json.loads(run.stdout[run.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result\nSTDOUT:\n{run.stdout[-2000:]}\nSTDERR:\n{run.stderr[-3000:]}')
    evidence = os.environ.get('STREAKFIT_RACE_EVIDENCE_STEP1')
    if evidence:
        with open(evidence, 'w') as f:
            json.dump(results, f, indent=1, default=str)
    return results


@pytest.mark.parametrize('scenario', [
    'R1_first_get_creates_one_mission',
    'R2_same_exercise_two_devices',
    'R3_last_two_exercises_two_devices_one_bonus',
    'R4_easing_races_a_completion',
    'R5_brain_boost_four_devices',
    'R6_effort_from_four_devices',
    'R7_deleted_mid_request_is_404',
])
def test_race(race_results, scenario):
    r = race_results[scenario]
    assert r['ok'], json.dumps([x for x in r['runs'] if not x['ok']], indent=1)
