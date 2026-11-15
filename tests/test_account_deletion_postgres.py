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


def _flask_db(env, *args):
    return subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                          cwd=REPO, env=env, capture_output=True, text=True)


def _nullable(engine, table, column):
    with engine.connect() as conn:
        return conn.execute(text(
            "select is_nullable from information_schema.columns "
            "where table_name = :t and column_name = :c"), {'t': table, 'c': column}).scalar()


def test_the_migration_round_trips_while_no_record_has_lost_its_person(migrated_pg):
    engine, env = migrated_pg
    assert _nullable(engine, 'report', 'reporter_user_id') == 'YES'
    assert _nullable(engine, 'appeal', 'user_id') == 'YES'
    down = _flask_db(env, 'downgrade', '47f7dc9962e3')
    assert down.returncode == 0, down.stderr[-2000:]
    assert _nullable(engine, 'report', 'reporter_user_id') == 'NO'
    assert _nullable(engine, 'appeal', 'user_id') == 'NO'
    up = _flask_db(env, 'upgrade')
    assert up.returncode == 0, up.stderr[-2000:]
    assert _nullable(engine, 'report', 'reporter_user_id') == 'YES'


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


def test_the_downgrade_refuses_once_a_reporter_has_left(migrated_pg):
    """Runs after the scenario, which deleted a reporter and an appellant."""
    engine, env = migrated_pg
    down = _flask_db(env, 'downgrade', '47f7dc9962e3')
    assert down.returncode != 0
    assert 'belong to deleted accounts' in down.stderr
    assert _nullable(engine, 'report', 'reporter_user_id') == 'YES'


def test_deletion_races_answer_cleanly_on_postgres(migrated_pg):
    """Deletion vs. operator actions and report filing, each ordering both ways,
    with the second request proven to wait on the first one's lock."""
    _engine, env = migrated_pg
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'account_deletion_race.py')],
        cwd=REPO, env={**env, 'PYTHONPATH': REPO}, capture_output=True, text=True,
        timeout=300)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the race script\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v['detail'] for k, v in out.items() if not v['ok']}
    assert not failed and result.returncode == 0, failed


def test_team_ownership_races_answer_cleanly_on_postgres(migrated_pg):
    """PR #23 follow-up: the owner fallback when its candidate deletes at the
    same moment (D2, never a 500), a member leaving while the legacy owner
    deletes in both orders (D4, no deadlock), a join during a sole owner's
    deletion (404) and two hand-overs at once (they serialize)."""
    _engine, env = migrated_pg
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'team_owner_race.py')],
        cwd=REPO, env={**env, 'PYTHONPATH': REPO}, capture_output=True, text=True,
        timeout=300)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the race script\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v['detail'] for k, v in out.items() if not v['ok']}
    assert not failed and result.returncode == 0, failed


def test_ownerless_teams_race_cleanly_on_postgres(migrated_pg):
    """Round 6 (owner decision #1): a post racing the change to ownerless, in
    both orders (it lands only while the team is owned); a mission completion
    against it; P9's kept team against its owner's own post; the empty-team
    cleanup against a legal hold in both orders, the photo sweep, and a
    failure half-way. See tests/ownerless_race.py."""
    _engine, env = migrated_pg
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'ownerless_race.py')],
        cwd=REPO, env={**env, 'PYTHONPATH': REPO}, capture_output=True, text=True,
        timeout=300)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the race script\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v['detail'] for k, v in out.items() if not v['ok']}
    assert not failed and result.returncode == 0, failed
    assert len(out) >= 30, sorted(out)


@pytest.mark.parametrize('scenario', ['S5', 'S5c', 'S6', 'S1'])
def test_account_deletion_takes_its_user_locks_in_one_ascending_pass(migrated_pg, scenario):
    """PR #23 follow-up review DEF-2 and DEF-3, the paused interleavings that
    deadlocked on b365d16: a late joiner the fallback prefers (S5: recorded as
    adult; S5c: every older member deleted meanwhile) leaving while the
    legacy owner's deletion holds the team, and a lower-id member reporting
    the deleting person (S6). Also the round-2 review's S1: the unlocked dry
    run must read a team's members and their accounts as one snapshot, or a
    member deleting at that instant gives a spurious 409 (409 on 3b143a1).
    See tests/team_owner_race.py."""
    _engine, env = migrated_pg
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'team_owner_race.py'), 'lock-order', scenario],
        cwd=REPO, env={**env, 'PYTHONPATH': REPO}, capture_output=True, text=True,
        timeout=300)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the race script\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v['detail'] for k, v in out.items() if not v['ok']}
    assert not failed and result.returncode == 0, failed


@pytest.mark.parametrize('scenario', ['SW1', 'SW1L', 'SW2', 'SW3', 'SW4', 'SW5',
                                      'SK1', 'SK2', 'LH1', 'ORD',
                                      'HEAP', 'NB', 'CRM', 'CRR', 'CRT',
                                      'LHM1', 'LHM2', 'LHM3', 'LHM4', 'LHM5'])
def test_the_photo_sweep_never_deadlocks_with_team_or_account_deletion(migrated_pg, scenario):
    """PR #23 round-2 review R2-DEF-A: the expired-photo sweep vs a sole owner's
    team deletion (leave or account), a photo sender's account deletion, a team
    deleted mid-batch -- each paused at the statement that deadlocked or failed
    on 3b143a1 -- plus SKIP LOCKED never dropping a due photo, legal holds, and
    the sweep's statement order. Round 3 review: the sender's photos stored out
    of id order (HEAP, NEW-1), a deletion never waiting for the whole pass (NB,
    NEW-2), failures mid-team and mid-run (CRM, CRR, CRT), and a legal hold
    placed at every point of an erase transaction (LHM1-5). See
    tests/team_photo_sweep_race.py."""
    _engine, env = migrated_pg
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'team_photo_sweep_race.py'), scenario],
        cwd=REPO, env={**env, 'PYTHONPATH': REPO}, capture_output=True, text=True,
        timeout=300)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the race script\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v['detail'] for k, v in out.items() if not v['ok']}
    assert not failed and result.returncode == 0, failed
