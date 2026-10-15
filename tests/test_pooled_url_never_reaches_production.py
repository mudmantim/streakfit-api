"""Staging step S0.7 6b (pooled Neon URL) cannot reach production by mistake.

The incident of 2026-10-13 was a production connection string pasted into
staging. Step 6b asks the owner to paste ANOTHER string (the pooled one), so
this replays the same mistake with the pooled form of production's host and
proves, in separate processes exactly as Render runs them, that:

  * `render_env_probe.py` (first in the Pre-Deploy command) makes no connection;
  * `flask db upgrade` refuses before ANY socket is opened, whether the
    production URL arrives as DATABASE_URL or as MIGRATION_DATABASE_URL.

Every socket connect in the child processes is intercepted (sitecustomize):
it is recorded to a file and refused, so nothing can leave the machine even if
a guard were broken, and the test can tell that it tried.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROD_POOLED = ('postgresql://neondb_owner:' + 'not-a-' + 'password'
               '@ep-odd-star-ajye3lag-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require')
STAGING_DIRECT = ('postgresql://neondb_owner:' + 'not-a-' + 'password'
                  '@ep-fake-staging-00000000.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require')
STAGING_SVC = 'srv-fakestaging000000000'

SITECUSTOMIZE = '''
import os, socket
_log = os.environ['CONNECT_LOG']
def _refuse(*args, **kwargs):
    with open(_log, 'a') as f:
        f.write(repr(args[-1] if args else '?') + '\\n')
    raise OSError('network blocked by test')
socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
'''


@pytest.fixture
def blocked(tmp_path):
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'sitecustomize.py').write_text(SITECUSTOMIZE)
    log = tmp_path / 'connects.log'

    def run(args, **env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ('RENDER', 'RENDER_SERVICE_ID', 'STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER',
                            'MIGRATION_DATABASE_URL', 'STREAKFIT_DEPLOYMENT', 'DATABASE_URL')}
        env.update(PYTHONPATH=str(site), CONNECT_LOG=str(log), FLASK_APP='app', SECRET_KEY='pooled-test',
                   JWT_SECRET_KEY='pooled-test-jwt-0123456789abcdef0123456789', PYTHONDONTWRITEBYTECODE='1',
                   RENDER='true', RENDER_SERVICE_ID=STAGING_SVC, STREAKFIT_DEPLOYMENT='staging', **env_extra)
        p = subprocess.run([sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
        attempts = log.read_text().splitlines() if log.exists() else []
        return p, attempts
    return run


def test_the_blocker_itself_works(blocked):
    p, attempts = blocked(['-c', 'import socket; socket.create_connection(("192.0.2.1", 5432), timeout=1)'])
    assert p.returncode != 0 and len(attempts) == 1


def test_the_pre_deploy_probe_makes_no_connection(blocked):
    p, attempts = blocked(['scripts/render_env_probe.py', 'pre-deploy'], DATABASE_URL=PROD_POOLED)
    assert p.returncode == 0 and attempts == []
    assert 'DATABASE_URL endpoint=ep-odd-star-ajye3lag' in p.stdout      # -pooler stripped: recognised
    assert 'not-a-password' not in p.stdout + p.stderr


@pytest.mark.parametrize('how', ['DATABASE_URL', 'MIGRATION_DATABASE_URL'])
def test_migration_with_productions_pooled_url_is_refused_before_any_connection(blocked, how):
    extra = {'DATABASE_URL': PROD_POOLED} if how == 'DATABASE_URL' else \
        {'DATABASE_URL': STAGING_DIRECT, 'MIGRATION_DATABASE_URL': PROD_POOLED}
    p, attempts = blocked(['-m', 'flask', 'db', 'upgrade'], **extra)
    out = p.stdout + p.stderr
    assert p.returncode != 0
    assert 'Refusing to start: R2' in out or 'Refusing to start: R3' in out, out[-1500:]
    assert 'Error: R' not in out          # one wording for every refusal
    assert 'ep-odd-star-ajye3lag' in out
    assert attempts == [], f'a connection was attempted before the refusal: {attempts}'
    assert 'not-a-password' not in out
