"""Review probe 4: what the still-serving PW-1 build answers between
"downgrade" and "previous build live" (the documented rollback order).

python -I scripts/rehearse_pw1_rollback_window.py <pw1 tree> <database url>  -> JSON
The database must be an EMPTY, DISPOSABLE one: this migrates it, uses it and
downgrades it. Shows why Rollback B causes an outage that /health hides.
"""
import json
import os
import subprocess
import sys

TREE, URL = os.path.abspath(sys.argv[1]), sys.argv[2]
sys.path.insert(0, TREE)
os.chdir(TREE)
env = dict(os.environ, DATABASE_URL=URL, FLASK_APP='app', SECRET_KEY='probe',
           JWT_SECRET_KEY='probe-jwt-0123456789abcdef0123456789', PYTHONPATH=TREE)
env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
assert subprocess.run([sys.executable, '-m', 'flask', 'db', 'upgrade'], env=env,
                      capture_output=True).returncode == 0
os.environ.update(DATABASE_URL=URL, SECRET_KEY='probe', JWT_SECRET_KEY='probe-jwt-0123456789abcdef0123456789')

import app as A  # noqa: E402

A.limiter.enabled = False
A.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
c = A.app.test_client()
out = {}
with A.app.app_context():
    c.post('/api/register', json={'username': 'probe_rb', 'password': 'Probe-Original-1'})
    tok = c.post('/api/login', json={'username': 'probe_rb', 'password': 'Probe-Original-1'}).get_json()['access_token']
    h = {'Authorization': 'Bearer ' + tok}
    out['before'] = {'me': c.get('/api/me', headers=h).status_code}
    A.db.session.remove()
    down = subprocess.run([sys.executable, '-m', 'flask', 'db', 'downgrade', 'd4f1b6e2a9c3'],
                          env=dict(env, PGOPTIONS='-c lock_timeout=5s'), capture_output=True, text=True)
    out['downgrade_rc'] = down.returncode
    window = {}
    for name, fn in [('health', lambda: c.get('/health')),
                     ('api_health', lambda: c.get('/api/health')),
                     ('demo_daily', lambda: c.get('/api/demo/daily')),
                     ('login', lambda: c.post('/api/login', json={'username': 'probe_rb', 'password': 'Probe-Original-1'})),
                     ('register', lambda: c.post('/api/register', json={'username': 'probe_rb2', 'password': 'Probe-Original-2'})),
                     ('me', lambda: c.get('/api/me', headers=h)),
                     ('daily', lambda: c.get('/api/daily', headers=h))]:
        A.db.session.remove()
        r = fn()
        window[name] = r.status_code
    out['window'] = window
print(json.dumps(out))
