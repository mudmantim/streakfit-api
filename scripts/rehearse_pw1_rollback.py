"""Rehearse the PW-1 zero-downtime rollback on a DISPOSABLE PostgreSQL server.

    python -I scripts/rehearse_pw1_rollback.py <pw1 tree> <compat tree> <server url>

<server url> must name a disposable PostgreSQL server: this creates one
scratch database there, uses it, and drops it. It never touches anything else.

Every build boots the way production does -- `import app` with
STREAKFIT_ENFORCE_DB_HEAD=1 -- so the head check is ON throughout. Each phase
is a separate process running one tree against the same database:

  1. PW-1 serving: two people change their password (one to a full-width
     one); each has a second device whose session that change revoked.
  2. ROLLBACK to the compat build, no migration: revoked sessions must stay
     revoked on EVERY authenticated route; new passwords work, old ones do
     not; PW-1's tokens keep working; password change is gone.
  3. ROLL FORWARD to PW-1, no migration: versions intact, compat's tokens
     still valid (they carry `tv`), revoked ones still refused, and a new
     change still revokes.
  4. Failure conditions, in Render's order (Pre-Deploy, then boot): a
     downgraded database is re-upgraded by the compat build's Pre-Deploy and
     then boots; a newer head fails Pre-Deploy (and boot would refuse).

Prints the observations as JSON and exits 1 if any expectation failed.
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

ALICE_OLD, ALICE_NEW = 'Alice-original-pass-1', 'Alice-changed-lantern-2'
CAROL_OLD, CAROL_NEW_WIDE = 'Carol-original-pass-1', 'Ｃａｒｏｌ-changed-lantern-3'
CAROL_NEW_TYPED = 'Carol-changed-lantern-3'          # the same password, ordinary keyboard

WALK_ROUTES = r"""
import re
bad, n = [], 0
for rule in A.app.url_map.iter_rules():
    if rule.endpoint == 'static':
        continue
    url = re.sub(r'<[^>]+>', '1', rule.rule)
    for m in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
        kw = {'json': {}} if m in ('POST', 'PUT', 'PATCH', 'DELETE') else {}
        if getattr(c, m.lower())(url, **kw).status_code != 401:
            continue
        r = getattr(c, m.lower())(url, headers=H(REVOKED), **kw)
        n += 1
        if not (r.status_code == 401 and (r.get_json(silent=True) or {}).get('error') == 'session_ended'):
            bad.append([m, rule.rule, r.status_code])
out['walk_routes'] = n
out['walk_not_refused'] = bad
"""


def flask(tree, *args, extra=None):
    return subprocess.run([PY, '-m', 'flask', 'db', *args], cwd=tree, capture_output=True, text=True,
                          env={**BASE_ENV, 'PYTHONPATH': tree, **(extra or {})}, timeout=300)


def predeploy(tree):
    r = flask(tree, 'upgrade')
    return {'rc': r.returncode, 'ran_upgrade': 'Running upgrade' in r.stderr}


def boot(tree):
    r = subprocess.run([PY, '-c', 'import app'], cwd=tree, capture_output=True, text=True,
                       env={**BASE_ENV, 'PYTHONPATH': tree, 'STREAKFIT_ENFORCE_DB_HEAD': '1'}, timeout=120)
    return {'rc': r.returncode, 'passed': 'Database migration check passed' in r.stderr,
            'refused': 'Refusing to start' in r.stderr}


def serve(tree, code, **values):
    prog = ("import json\nimport app as A\nA.limiter.enabled = False\n"
            "A.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)\n"
            "c = A.app.test_client()\nout = {}\n"
            "def H(t): return {'Authorization': 'Bearer ' + t}\n"
            + "".join(f"{k} = {v!r}\n" for k, v in values.items())
            + "with A.app.app_context():\n"
            + "\n".join('    ' + line for line in code.strip('\n').split('\n'))
            + "\nprint(json.dumps(out))\n")
    r = subprocess.run([PY, '-c', prog], cwd=tree, capture_output=True, text=True,
                       env={**BASE_ENV, 'PYTHONPATH': tree, 'STREAKFIT_ENFORCE_DB_HEAD': '1'}, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def versions():
    eng = create_engine(URL)
    try:
        with eng.connect() as c:
            return dict(c.execute(text('SELECT username, token_version FROM "user" ORDER BY id')).all())
    finally:
        eng.dispose()


obs, failures = {}, []


def expect(label, ok):
    if not ok:
        failures.append(label)


try:
    # 1. PW-1 serving.
    obs['pw1_predeploy'] = predeploy(PW1)
    obs['pw1_boot'] = boot(PW1)
    t = serve(PW1, """
for u, p in (('reh_alice', ALICE_OLD), ('reh_carol', CAROL_OLD)):
    assert c.post('/api/register', json={'username': u, 'password': p}).status_code == 201
def login(u, p):
    return c.post('/api/login', json={'username': u, 'password': p}).get_json()['access_token']
a_phone, a_tablet = login('reh_alice', ALICE_OLD), login('reh_alice', ALICE_OLD)
c_phone, c_tablet = login('reh_carol', CAROL_OLD), login('reh_carol', CAROL_OLD)
ra = c.post('/api/me/password', json={'current_password': ALICE_OLD, 'new_password': ALICE_NEW}, headers=H(a_phone))
rc = c.post('/api/me/password', json={'current_password': CAROL_OLD, 'new_password': CAROL_NEW_WIDE}, headers=H(c_phone))
out.update(change_alice=ra.status_code, change_carol=rc.status_code,
           a_new=ra.get_json()['access_token'], c_new=rc.get_json()['access_token'],
           a_tablet=a_tablet, c_tablet=c_tablet,
           a_tablet_now=c.get('/api/me', headers=H(a_tablet)).status_code)
""", ALICE_OLD=ALICE_OLD, ALICE_NEW=ALICE_NEW, CAROL_OLD=CAROL_OLD, CAROL_NEW_WIDE=CAROL_NEW_WIDE)
    obs['pw1'] = {k: t[k] for k in ('change_alice', 'change_carol', 'a_tablet_now')}
    obs['versions_after_pw1'] = versions()
    expect('PW-1 changes succeed', t['change_alice'] == 200 and t['change_carol'] == 200)
    expect('PW-1 revokes the tablet', t['a_tablet_now'] == 401)

    # 2. ROLLBACK to the compat build.
    obs['compat_predeploy'] = predeploy(COMPAT)
    obs['compat_boot'] = boot(COMPAT)
    expect('compat Pre-Deploy migrates nothing', obs['compat_predeploy'] == {'rc': 0, 'ran_upgrade': False})
    expect('compat boots with the head check on', obs['compat_boot']['passed'] and obs['compat_boot']['rc'] == 0)
    k = serve(COMPAT, WALK_ROUTES + """
def st(r): return [r.status_code, (r.get_json(silent=True) or {}).get('error')]
out['tablet_me'] = st(c.get('/api/me', headers=H(A_TABLET)))
out['tablet_delete'] = st(c.delete('/api/me', json={'password': ALICE_NEW}, headers=H(A_TABLET)))
out['carol_tablet_daily'] = st(c.get('/api/daily', headers=H(C_TABLET)))
out['alice_new_pw'] = c.post('/api/login', json={'username': 'reh_alice', 'password': ALICE_NEW}).status_code
out['alice_old_pw'] = c.post('/api/login', json={'username': 'reh_alice', 'password': ALICE_OLD}).status_code
out['carol_new_typed'] = c.post('/api/login', json={'username': 'reh_carol', 'password': CAROL_NEW_TYPED}).status_code
out['carol_new_wide'] = c.post('/api/login', json={'username': 'reh_carol', 'password': CAROL_NEW_WIDE}).status_code
out['carol_old_pw'] = c.post('/api/login', json={'username': 'reh_carol', 'password': CAROL_OLD}).status_code
out['pw1_token_still_valid'] = c.get('/api/me', headers=H(A_NEW)).status_code
ct = c.post('/api/login', json={'username': 'reh_alice', 'password': ALICE_NEW}).get_json()['access_token']
from flask_jwt_extended import decode_token
out['compat_token_tv'] = decode_token(ct)['tv']
out['compat_token'] = ct
out['password_change_route'] = c.post('/api/me/password', json={'current_password': ALICE_NEW, 'new_password': 'Another-long-one-77'}, headers=H(ct)).status_code
def reg(u, p):
    r = c.post('/api/register', json={'username': u, 'password': p})
    return [r.status_code, (r.get_json(silent=True) or {}).get('code')]
out['register_short'] = reg('reh_short', 'Bob8char')
out['register_common'] = reg('reh_common', 'passwordpassword')
out['register'] = reg('reh_bob', 'Bob-lantern-kettle-9')
out['bob_token'] = c.post('/api/login', json={'username': 'reh_bob', 'password': 'Bob-lantern-kettle-9'}).get_json()['access_token']
""", REVOKED=t['a_tablet'], A_TABLET=t['a_tablet'], C_TABLET=t['c_tablet'], A_NEW=t['a_new'],
        ALICE_NEW=ALICE_NEW, ALICE_OLD=ALICE_OLD, CAROL_NEW_TYPED=CAROL_NEW_TYPED,
        CAROL_NEW_WIDE=CAROL_NEW_WIDE, CAROL_OLD=CAROL_OLD)
    obs['compat'] = {key: v for key, v in k.items() if key not in ('compat_token', 'bob_token')}
    obs['versions_after_compat'] = versions()
    expect('compat: every authenticated route refuses a revoked session',
           k['walk_routes'] >= 40 and k['walk_not_refused'] == [])
    expect('compat: revoked tablet refused (me, delete, daily)',
           k['tablet_me'] == [401, 'session_ended'] and k['tablet_delete'] == [401, 'session_ended']
           and k['carol_tablet_daily'] == [401, 'session_ended'])
    expect('compat: new passwords work, old ones do not',
           k['alice_new_pw'] == 200 and k['alice_old_pw'] == 401 and k['carol_new_typed'] == 200
           and k['carol_new_wide'] == 200 and k['carol_old_pw'] == 401)
    expect('compat: PW-1 tokens keep working', k['pw1_token_still_valid'] == 200)
    expect('compat: its tokens carry the current version', k['compat_token_tv'] == 1)
    expect('compat: no password change', k['password_change_route'] in (404, 405))
    expect('compat: sign-up keeps PW-1 policy',
           k['register_short'] == [400, 'too_short'] and k['register_common'] == [400, 'common']
           and k['register'] == [201, None])
    expect('compat: versions untouched', obs['versions_after_compat']['reh_alice'] == 1
           and obs['versions_after_compat']['reh_carol'] == 1)

    # 3. ROLL FORWARD.
    obs['pw1_again_predeploy'] = predeploy(PW1)
    obs['pw1_again_boot'] = boot(PW1)
    expect('roll-forward migrates nothing and boots',
           obs['pw1_again_predeploy'] == {'rc': 0, 'ran_upgrade': False} and obs['pw1_again_boot']['passed'])
    f = serve(PW1, """
def st(r): return [r.status_code, (r.get_json(silent=True) or {}).get('error')]
out['compat_token'] = st(c.get('/api/me', headers=H(COMPAT_TOKEN)))
out['bob_compat_token'] = st(c.get('/api/me', headers=H(BOB)))
out['tablet'] = st(c.get('/api/me', headers=H(A_TABLET)))
r = c.post('/api/me/password', json={'current_password': ALICE_NEW, 'new_password': 'Alice-third-lantern-3'}, headers=H(A_NEW))
out['change_again'] = r.status_code
out['compat_token_after_change'] = st(c.get('/api/me', headers=H(COMPAT_TOKEN)))
""", COMPAT_TOKEN=k['compat_token'], BOB=k['bob_token'], A_TABLET=t['a_tablet'], A_NEW=t['a_new'],
        ALICE_NEW=ALICE_NEW)
    obs['roll_forward'] = f
    obs['versions_after_roll_forward'] = versions()
    expect('roll-forward: compat sessions survive (no forced sign-out)',
           f['compat_token'] == [200, None] and f['bob_compat_token'] == [200, None])
    expect('roll-forward: the revoked tablet stays revoked', f['tablet'] == [401, 'session_ended'])
    expect('roll-forward: a new change revokes the compat session',
           f['change_again'] == 200 and f['compat_token_after_change'] == [401, 'session_ended'])

    # 4. Failure conditions, as Render runs a deploy: Pre-Deploy
    #    (`flask db upgrade`) FIRST, then boot with the head check.
    #    a) Database downgraded to d4f1b6e2a9c3: the boot check alone would
    #       refuse, but Pre-Deploy runs first, and the compat build carries
    #       e8b3c1f0a7d2 -- so it RE-APPLIES the migration and then boots.
    obs['downgrade_rc'] = flask(PW1, 'downgrade', 'd4f1b6e2a9c3',
                                extra={'PGOPTIONS': '-c lock_timeout=5s'}).returncode
    obs['compat_boot_alone_on_d4f1'] = boot(COMPAT)
    obs['compat_predeploy_on_d4f1'] = predeploy(COMPAT)
    obs['compat_boot_after_predeploy_on_d4f1'] = boot(COMPAT)
    expect('boot check alone refuses a downgraded head', obs['compat_boot_alone_on_d4f1']['refused'])
    expect('on Render, Pre-Deploy re-applies e8b3c1f0a7d2 and the build boots',
           obs['compat_predeploy_on_d4f1'] == {'rc': 0, 'ran_upgrade': True}
           and obs['compat_boot_after_predeploy_on_d4f1']['passed'])
    #    b) A NEWER head (a later migration the compat build does not know):
    #       Pre-Deploy fails (Alembic cannot locate the revision), so the
    #       deploy stops before boot; and boot would refuse anyway.
    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = 'f0f0f0f0f0f0'"))
    eng.dispose()
    obs['compat_predeploy_on_future_head'] = predeploy(COMPAT)
    obs['compat_boot_on_future_head'] = boot(COMPAT)
    expect('Pre-Deploy fails on a newer head', obs['compat_predeploy_on_future_head']['rc'] != 0)
    expect('boot refuses a newer head', obs['compat_boot_on_future_head']['refused'])
finally:
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()

obs['failures'] = failures
print(json.dumps(obs, indent=1))
sys.exit(1 if failures else 0)
