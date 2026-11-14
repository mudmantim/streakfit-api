"""DEF-1 timing on PostgreSQL: OPT-IN benchmark, not a CI gate.

Runs tests/d1_timing_benchmark.py (about a minute; 3,000 interleaved samples
per series) and passes when a kept-empty team and a deleted one are
indistinguishable by time on every endpoint a former owner can call:
|delta median| <= 0.05 ms, its bootstrap 95% CI within +-0.05 ms, and a
one-request attacker's balanced accuracy <= 0.60.

Skipped unless BOTH are set:
  STREAKFIT_TEST_POSTGRES_URL  an EMPTY, disposable database (migrated here);
  STREAKFIT_D1_TIMING=1        deliberately asking for a timing run.
The CI job does not set the second: timing on shared runners is too noisy to
gate on. The deterministic gate is test_d1_query_parity_* (same SQL).
"""
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PG_URL = os.environ.get('STREAKFIT_TEST_POSTGRES_URL')

pytestmark = pytest.mark.skipif(
    not PG_URL or os.environ.get('STREAKFIT_D1_TIMING') != '1',
    reason='opt-in: set STREAKFIT_TEST_POSTGRES_URL and STREAKFIT_D1_TIMING=1')


def test_a_kept_team_and_a_deleted_one_take_the_same_time():
    engine = create_engine(PG_URL)
    with engine.connect() as conn:
        tables = conn.execute(text(
            "select count(*) from information_schema.tables "
            "where table_schema = 'public'")).scalar()
    engine.dispose()
    assert tables == 0, 'STREAKFIT_TEST_POSTGRES_URL must point at an empty database'
    env = {**os.environ, 'DATABASE_URL': PG_URL, 'FLASK_APP': 'app',
           'SECRET_KEY': 'test', 'JWT_SECRET_KEY': 'test', 'PYTHONPATH': REPO}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    up = subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'],
                        cwd=REPO, env=env, capture_output=True, text=True)
    assert up.returncode == 0, up.stderr[-3000:]
    result = subprocess.run(
        [sys.executable, os.path.join('tests', 'd1_timing_benchmark.py')],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=1800)
    try:
        out = json.loads(result.stdout[result.stdout.index('{'):])
    except ValueError:
        pytest.fail(f'no result from the benchmark\nSTDOUT:\n{result.stdout[-2000:]}'
                    f'\nSTDERR:\n{result.stderr[-3000:]}')
    failed = {k: v for k, v in out['endpoints'].items() if not v['pass']}
    assert not failed and result.returncode == 0, (failed, out['parity'])
