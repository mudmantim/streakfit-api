"""Rehearse the PW-1 zero-downtime rollback on a disposable PostgreSQL database.

python -I scripts/rehearse_pw1_rollback.py <pw1 tree> <compat tree> <server url>

<server url> must be a DISPOSABLE PostgreSQL server: this creates and drops
its own scratch database there, and never touches any other database.
First run 2026-10-12 against PostgreSQL 17.10: see
docs/operations/deploy-pw1-password-change.md, Rollback A.

Every phase is a separate process running one build against the same
database, with STREAKFIT_ENFORCE_DB_HEAD=1 wherever a build boots -- the head
check is never disabled. Prints one JSON object of observations.
"""
import json
import os
import subprocess
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PW1, COMPAT, SERVER = (os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]), sys.argv[3])
PY = sys.executable
admin = create_engine(make_url(SERVER).set(database='postgres'), isolation_level='AUTOCOMMIT')
name = f'sf_rb_{uuid.uuid4().hex[:8]}'
with admin.connect() as c:
    c.execute(text(f'CREATE DATABASE "{name}"'))
URL = make_url(SERVER).set(database=name).render_as_string(hide_password=False)
BASE_ENV = {**os.environ, 'DATABASE_URL': URL, 'FLASK_APP': 'app', 'SECRET_KEY': 'rehearsal',
            'JWT_SECRET_KEY': 'rehearsal-jwt-0123456789abcdef0123456789'}
for k in ('STREAKFIT_ENFORCE_DB_HEAD', 'STREAKFIT_RETENTION_SWEEPER', 'STREAKFIT_AGE_POLICY'):
    BASE_ENV.pop(k, None)


def flask(tree, *args, extra=None):
    return subprocess.run([PY, '-m', 'flask', 'db', *args], cwd=tree, capture_output=True, text=True,
                          env={**BASE_ENV, 'PYTHONPATH': tree, **(extra or {})}, timeout=300)


def boot(tree):
    """What gunicorn --preload does first: import the app with the head check on."""
    r = subprocess.run([PY, '-c', 'import app'], cwd=tree, capture_output=True, text=True,
                       env={**BASE_ENV, 'PYTHONPATH': tree, 'STREAKFIT_ENFORCE_DB_HEAD': '1'}, timeout=120)
    return {'rc': r.returncode,
            'passed': 'Database migration check passed' in r.stderr,
            'refused': 'Refusing to start' in r.stderr}


def serve(tree, code):
    """Run requests against a build (head check on at import, like production)."""
    prog = ("import json, sys\nimport app as A\nA.limiter.enabled = False\n"
            "A.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)\n"
            "c = A.app.test_client()\nout = {}\n"
            "def H(t): return {'Authorization': 'Bearer ' + t}\n"
            "with A.app.app_context():\n" + "\n".join('    ' + line for line in code.strip().split('\n'))
            + "\nprint(json.dumps(out))\n")
    r = subprocess.run([PY, '-c', prog], cwd=tree, capture_output=True, text=True,
                       env={**BASE_ENV, 'PYTHONPATH': tree, 'STREAKFIT_ENFORCE_DB_HEAD': '1'}, timeout=300)
    if r.returncode != 0:
        return {'error': r.stderr[-1500:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def versions():
    eng = create_engine(URL)
    try:
        with eng.connect() as c:
            return dict(c.execute(text('SELECT username, token_version FROM "user" ORDER BY id')).all())
    finally:
        eng.dispose()


obs = {}
try:
    # 1. Production today, then the PW-1 release.
    obs['pw1_upgrade'] = flask(PW1, 'upgrade').returncode
    obs['pw1_boot'] = boot(PW1)
    obs['pw1_traffic'] = serve(PW1, """
c.post('/api/register', json={'username': 'reh_alice', 'password': 'Alice-original-pass-1'})
phone = c.post('/api/login', json={'username': 'reh_alice', 'password': 'Alice-original-pass-1'}).get_json()['access_token']
tablet = c.post('/api/login', json={'username': 'reh_alice', 'password': 'Alice-original-pass-1'}).get_json()['access_token']
r = c.post('/api/me/password', json={'current_password': 'Alice-original-pass-1', 'new_password': 'Alice-changed-lantern-2'}, headers=H(phone))
out['change'] = r.status_code
out['new_token'] = r.get_json()['access_token']
out['tablet'] = tablet
out['tablet_status'] = c.get('/api/me', headers=H(tablet)).status_code
""")
    obs['versions_after_pw1'] = versions()
    tokens = obs['pw1_traffic']

    # 2. ROLLBACK: deploy the compat build. No downgrade.
    obs['compat_predeploy'] = (lambda r: {'rc': r.returncode, 'ran_upgrade': 'Running upgrade' in r.stderr})(
        flask(COMPAT, 'upgrade'))
    obs['compat_boot'] = boot(COMPAT)
    obs['compat_traffic'] = serve(COMPAT, f"""
out['login_new_pw'] = c.post('/api/login', json={{'username': 'reh_alice', 'password': 'Alice-changed-lantern-2'}}).status_code
out['login_old_pw'] = c.post('/api/login', json={{'username': 'reh_alice', 'password': 'Alice-original-pass-1'}}).status_code
out['pw1_token'] = c.get('/api/me', headers=H({tokens['new_token']!r})).status_code
out['revoked_tablet_token'] = c.get('/api/me', headers=H({tokens['tablet']!r})).status_code
out['register_new'] = c.post('/api/register', json={{'username': 'reh_bob', 'password': 'Bob8char'}}).status_code
bob = c.post('/api/login', json={{'username': 'reh_bob', 'password': 'Bob8char'}}).get_json()['access_token']
out['bob_token'] = bob
alice = c.post('/api/login', json={{'username': 'reh_alice', 'password': 'Alice-changed-lantern-2'}}).get_json()['access_token']
out['alice_compat_token'] = alice
out['password_route'] = c.post('/api/me/password', json={{}}, headers=H(alice)).status_code
out['daily'] = c.get('/api/daily', headers=H(alice)).status_code
""")
    obs['versions_after_compat'] = versions()
    compat = obs['compat_traffic']

    # 3. ROLL FORWARD to PW-1 again: nothing to migrate, versions intact.
    obs['pw1_again_predeploy'] = (lambda r: {'rc': r.returncode, 'ran_upgrade': 'Running upgrade' in r.stderr})(
        flask(PW1, 'upgrade'))
    obs['pw1_again_boot'] = boot(PW1)
    obs['pw1_again_traffic'] = serve(PW1, f"""
out['alice_pw1_token'] = c.get('/api/me', headers=H({tokens['new_token']!r})).status_code
out['alice_compat_token'] = (lambda r: (r.status_code, r.get_json().get('error')))(c.get('/api/me', headers=H({compat['alice_compat_token']!r})))
out['revoked_tablet'] = (lambda r: (r.status_code, r.get_json().get('error')))(c.get('/api/me', headers=H({tokens['tablet']!r})))
out['bob_compat_token'] = c.get('/api/me', headers=H({compat['bob_token']!r})).status_code
out['bob_login_8char'] = c.post('/api/login', json={{'username': 'reh_bob', 'password': 'Bob8char'}}).status_code
""")
    obs['versions_after_roll_forward'] = versions()

    # 4. Failure conditions for the compat build.
    obs['downgraded_then_compat_boot'] = None
    down = flask(PW1, 'downgrade', 'd4f1b6e2a9c3', extra={'PGOPTIONS': '-c lock_timeout=5s'})
    obs['downgrade_rc'] = down.returncode
    obs['compat_boot_on_d4f1'] = boot(COMPAT)              # must refuse: not its head
    flask(PW1, 'upgrade')
    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = 'f0f0f0f0f0f0'"))
    eng.dispose()
    obs['compat_boot_on_future_head'] = boot(COMPAT)       # e.g. after a later migration
finally:
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()
for k in ('pw1_traffic', 'compat_traffic'):
    for t in ('new_token', 'tablet', 'bob_token', 'alice_compat_token'):
        if isinstance(obs.get(k), dict) and t in obs[k]:
            obs[k][t] = '<token>'
print(json.dumps(obs, indent=1))
