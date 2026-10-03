"""RC-B2a: concurrency and data integrity on PostgreSQL, the engine production runs.

Opt-in: STREAKFIT_TEST_POSTGRES_URL must point at an EMPTY, disposable
database. It is built with `flask db upgrade` (the migration chain, so the
constraints tested are the ones production gets), then
tests/concurrency_race.py runs every scenario in a subprocess and reports, per
scenario, whether the REPAIRED behaviour held: the invariant AND the intended
lock wait observed in pg_stat_activity / pg_blocking_pids. See that script's
docstring for why a timeout is never a pass.
"""
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')

pytestmark = pytest.mark.skipif(not PG_URL, reason='STREAKFIT_TEST_POSTGRES_URL not set')


@pytest.fixture(scope='module')
def race_results():
    engine = create_engine(PG_URL)
    with engine.connect() as conn:
        tables = conn.execute(text(
            "select count(*) from information_schema.tables where table_schema = 'public'")).scalar()
    engine.dispose()
    assert tables == 0, 'STREAKFIT_TEST_POSTGRES_URL must point at an empty database'
    env = {**os.environ, 'DATABASE_URL': PG_URL, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test', 'PYTHONPATH': REPO}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    up = subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'],
                        cwd=REPO, env=env, capture_output=True, text=True)
    assert up.returncode == 0, up.stderr[-3000:]
    run = subprocess.run([sys.executable, os.path.join('tests', 'concurrency_race.py')],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    try:
        results = json.loads(run.stdout[run.stdout.index('{'):])
        evidence = os.environ.get('STREAKFIT_RACE_EVIDENCE')
        if evidence:            # keep the raw per-scenario evidence when asked
            with open(evidence, 'w') as f:
                json.dump(results, f, indent=1, default=str)
        return results
    except ValueError:
        pytest.fail(f'no result\nSTDOUT:\n{run.stdout[-2000:]}\nSTDERR:\n{run.stderr[-3000:]}')


SCENARIOS = [
    'D26_concurrent_filter_purchases',
    'D27a_parallel_awards_lose_updates',
    'D27b_mission_bonus_paid_twice',
    'D27c_awards_across_routes',
    'D28_member_cap',
    'D29_same_exercise_twice',
    'D30_same_user_double_join',
    'D31_double_delete',
    'D32a_campfire_crossing_small_flame',
    'D32b_campfire_first_log',
    'D34_brain_boost_answered_twice',
    'D35_same_challenge_twice',
    'D36_challenge_daily_reward_cap',
    'X1_lock_timeout_503',
    'X2_delete_then_complete',
    'X3_complete_then_delete',
    'X4_report_during_completion',
]


@pytest.mark.parametrize('name', SCENARIOS)
def test_scenario(race_results, name):
    r = race_results[name]
    detail = {k: r.get(k) for k in ('outcome', 'invariants', 'intended_wait', 'state', 'error')}
    detail['t2_wait'] = (r.get('t2_wait') or {}).get('query'), (r.get('t2_wait') or {}).get('waiting_relation')
    assert r.get('fixed_ok'), json.dumps(detail, default=str)[:3000]
