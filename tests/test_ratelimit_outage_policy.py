"""D7 round 2: a rate-limit backend failure never buys anybody a larger allowance.

The owner's policy (2026-10-04), replacing "Option B":

  1. Redis failing never disables rate limiting; ordinary routes keep their
     own limits from process memory.
  2. Login drops to the strict degraded cap on the FIRST failure, including a
     write that fails after the view has run.
  3. Invite lookup refuses (503) on the FIRST failure.
  4. No allowance is split across a transition, in either direction, however
     often the backend flaps.

These are BLACK-BOX scenarios: each runs the application in a subprocess
against a fake Redis whose failure mode the scenario switches at exact moments
(tests/_fake_resp.py), and counts what the application actually let through.
They import nothing from the candidate's internals, so the same file runs
unchanged against earlier commits -- point STREAKFIT_D7_APP_DIR at another
checkout. Measured that way:

  * 6541cae (production, Option B): the first failure switches the limiter
    off: 12 of 12 registrations against 5/minute.
  * b045172 (round 1): the limiter stays on for one failure, then switches
    off on the second; Flask-Limiter's fallback starts from zero at each
    switch, so every transition is a fresh allowance.

The real-Valkey failure matrix (refused, DNS, hung, black-holed, slow, OOM,
read-only, flapping, recovery under traffic) is in the lab harness under
e2e-campaign/evidence/d7; these tests pin the same properties in CI.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
APP_DIR = Path(os.environ.get("STREAKFIT_D7_APP_DIR", TESTS.parent))

RUNNER = r"""
import json, logging, os, sys, tempfile, threading, time
sys.path.append(os.environ['D7_TESTS_DIR'])
import _fake_resp
srv = _fake_resp.FakeRedis()
os.environ['RATELIMIT_STORAGE_URI'] = srv.uri
fd, path = tempfile.mkstemp(suffix='.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + path
logging.disable(logging.CRITICAL)
import app as A
A.app.logger.disabled = True
with A.app.app_context():
    A.db.create_all()
A.limiter.enabled = True

# Make recovery observable in seconds rather than tens of seconds. Only the
# candidate has these knobs; an older commit simply runs as it is.
GATE = getattr(A, '_RateLimitGate', None)
if GATE is not None and os.environ.get('D7_FAST_RECOVERY') == '1':
    GATE.PROBE_INTERVAL_S = 0.3

c = A.app.test_client()
seq = [0]
enabled_seen = set()


def call(method, url, ip, **kw):
    t = time.monotonic()
    r = getattr(c, method)(url, environ_base={'REMOTE_ADDR': ip}, **kw)
    enabled_seen.add(bool(A.limiter.enabled))
    return r.status_code, round(time.monotonic() - t, 3), r


def register():
    seq[0] += 1
    s, dt, _ = call('post', '/api/register', '10.0.0.2',
                    json={'username': f'd7_reg_{seq[0]}', 'password': 'Correct1!pass'})
    return s, dt


def bad_login():
    s, dt, _ = call('post', '/api/login', '10.0.0.3',
                    json={'username': 'd7_nobody', 'password': 'wrong-password'})
    return s, dt


def event():
    s, dt, _ = call('post', '/api/events', '10.0.0.4', json={'event': 'd7'})
    return s, dt


def setup_team():
    def reg_login(name):
        call('post', '/api/register', '10.0.0.1',
             json={'username': name, 'password': 'Correct1!pass'})
        _, _, r = call('post', '/api/login', '10.0.0.1',
                       json={'username': name, 'password': 'Correct1!pass'})
        return r.get_json()['access_token']
    owner = reg_login('d7_owner')
    joiner = reg_login('d7_joiner')
    _, _, r = call('post', '/api/teams', '10.0.0.1', json={'name': 'D7'},
                   headers={'Authorization': f'Bearer {owner}'})
    return joiner, r.get_json()['team']['invite_code']


def lookup(token, code):
    s, dt, _ = call('get', f'/api/teams/lookup/{code}', '10.0.0.5',
                    headers={'Authorization': f'Bearer {token}'})
    return s, dt


def tally(results):
    return {'accepted': sum(1 for s, _ in results if s not in (429, 503, 500)),
            'limited': sum(1 for s, _ in results if s == 429),
            'refused': sum(1 for s, _ in results if s == 503),
            'errors': sum(1 for s, _ in results if s >= 500 and s != 503),
            'max_s': max([dt for _, dt in results] or [0]),
            'codes': [s for s, _ in results]}


def gate_state():
    g = A.limiter.storage
    return {k: getattr(g, k, None) for k in
            ('degraded', 'degrade_count', 'recover_count', 'probe_count')}


out = {}
name = os.environ['D7_SCENARIO']

if name == 'sustained_refused':
    # Backend refused from the first request, for longer than any old
    # re-probe interval: two bursts 2.5 s apart, all inside one minute.
    srv.set_mode('refuse')
    reg, ev, lg = [], [], []
    for _burst in range(2):
        reg += [register() for _ in range(6)]
        ev += [event() for _ in range(20)]
        lg += [bad_login() for _ in range(4)]
        time.sleep(2.5)
    out = {'register': tally(reg), 'events': tally(ev), 'login': tally(lg)}

elif name == 'write_refused':
    # A backend that answers reads and refuses writes (maxmemory+noeviction,
    # or a replica after failover): every count is a write.
    joiner, code = setup_team()
    srv.set_mode(os.environ.get('D7_WRITE_MODE', 'oom'))
    reg, lg, lk = [], [], []
    for _burst in range(2):
        reg += [register() for _ in range(6)]
        lg += [bad_login() for _ in range(4)]
        lk += [lookup(joiner, code) for _ in range(3)]
        time.sleep(2.5)
    out = {'register': tally(reg), 'login': tally(lg), 'lookup': tally(lk)}

elif name == 'first_failure':
    # Healthy, then the backend goes away. The very first request after it
    # does must already be on the degraded policy.
    joiner, code = setup_team()
    ok_lookup = lookup(joiner, code)
    srv.set_mode('refuse')
    out['lookup_before'] = ok_lookup[0]
    out['lookup_first'] = lookup(joiner, code)[0]
    out['login'] = tally([bad_login() for _ in range(6)])

elif name == 'split_at_failure':
    # Four of five registrations used against the shared backend, then it
    # fails. The allowance must carry on from four, not restart from zero.
    before = [register() for _ in range(4)]
    srv.set_mode('refuse')
    after = [register() for _ in range(6)]
    out = {'before': tally(before), 'after': tally(after)}

elif name == 'split_at_recovery':
    # The whole allowance used while the backend is away, then it comes back.
    # Coming back must not hand out a second allowance inside the window.
    srv.set_mode('refuse')
    during = [register() for _ in range(6)]
    srv.set_mode('ok')
    after = []
    deadline = time.monotonic() + float(os.environ.get('D7_RECOVERY_WAIT', '4'))
    while time.monotonic() < deadline:
        after.append(register())
        time.sleep(0.1)
    out = {'during': tally(during), 'after': tally(after),
           'backend_writes_after': srv.count('EVALSHA', 'ok'),
           'gate': gate_state()}

elif name == 'flapping':
    # The backend alternates between working and refused every 0.7 s while
    # an attacker keeps going, all inside one minute.
    joiner, code = setup_team()
    stop = threading.Event()
    def flap():
        while not stop.is_set():
            srv.set_mode('refuse'); time.sleep(0.7)
            srv.set_mode('ok'); time.sleep(0.7)
    th = threading.Thread(target=flap, daemon=True); th.start()
    reg, lg, lk, ev = [], [], [], []
    t_end = time.monotonic() + 8
    while time.monotonic() < t_end:
        reg.append(register()); lg.append(bad_login())
        lk.append(lookup(joiner, code)); ev.append(event())
        time.sleep(0.05)
    stop.set(); th.join()
    out = {'register': tally(reg), 'login': tally(lg), 'lookup': tally(lk),
           'events': tally(ev), 'gate': gate_state()}

elif name == 'deduct_write_fails':
    # The before-request hit and checks succeed, the view answers 401, and
    # the deferred deduction -- a write after the view -- is the first thing
    # to fail.
    srv.writes_allowed = 1      # the 10/minute hit; the deduction is refused
    first = bad_login()
    rest = [bad_login() for _ in range(6)]
    out = {'first': first[0], 'rest': tally(rest)}

elif name == 'hung':
    # Accepts connections and never answers. Bounded, and still limited.
    srv.set_mode('hang')
    reg, health = [], []
    for _burst in range(2):
        reg += [register() for _ in range(6)]
        health.append(call('get', '/api/health', '10.0.0.9')[:2])
        time.sleep(2.5)
    out = {'register': tally(reg), 'health': tally(health)}

elif name == 'degraded_is_quiet':
    # Once degraded, unthrottled routes and the self-check never wait on the
    # backend, and requests do not probe it more than once per interval.
    srv.set_mode('hang')
    register()                                   # the one bounded failure
    t0 = srv.count()
    health = [call('get', '/api/health', '10.0.0.9')[:2] for _ in range(5)]
    s, dt, r = call('get', '/api/verification/self', '10.0.0.8')
    checks = {x['id']: x for x in (r.get_json() or {}).get('checks', [])}
    regs = [register() for _ in range(10)]
    out = {'health': tally(health), 'self_s': dt,
           'rl_status': checks.get('ratelimit.shared_storage', {}).get('status'),
           'rl_observed': checks.get('ratelimit.shared_storage', {}).get('observed'),
           'policy': checks.get('ratelimit.outage_policy', {}).get('status'),
           'backend_commands_while_degraded': srv.count() - t0,
           'register': tally(regs)}

out['limiter_enabled_seen'] = sorted(enabled_seen)
print('D7RESULT ' + json.dumps(out), flush=True)
srv.stop()
os._exit(0)
"""


def _run(scenario, timeout=60, **env_extra):
    env = dict(os.environ)
    env.update(SECRET_KEY="test-secret", JWT_SECRET_KEY="test-jwt-secret",
               D7_SCENARIO=scenario, D7_TESTS_DIR=str(TESTS), **env_extra)
    env.pop("RATELIMIT_STORAGE_URI", None)
    try:
        p = subprocess.run([sys.executable, "-c", RUNNER], cwd=APP_DIR, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{scenario}: the application did not finish within {timeout}s")
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("D7RESULT ")]
    assert lines, f"{scenario} produced no result:\n{p.stdout[-2000:]}\n{p.stderr[-4000:]}"
    return json.loads(lines[-1][len("D7RESULT "):])


# ── 1. Never disabled, never unlimited ──────────────────────────────────────

def test_a_sustained_outage_never_switches_rate_limiting_off():
    """Measured on 6541cae: 12 of 12 registrations (5/minute) and 40 of 40
    events (30/minute). On b045172 the second burst ran unlimited."""
    r = _run("sustained_refused")
    assert r["limiter_enabled_seen"] == [True], r
    assert r["register"]["accepted"] == 5, r["register"]
    assert r["events"]["accepted"] == 30, r["events"]
    assert r["login"]["accepted"] <= 3, r["login"]
    for route in ("register", "events", "login"):
        assert r[route]["errors"] == 0, (route, r[route])


@pytest.mark.parametrize("mode", ["oom", "readonly"])
def test_a_backend_that_refuses_writes_is_a_failure_too(mode):
    r = _run("write_refused", D7_WRITE_MODE=mode)
    assert r["limiter_enabled_seen"] == [True], r
    # Two of the five went on setting up the test's team, from another IP;
    # this IP gets its own five.
    assert r["register"]["accepted"] <= 5, r["register"]
    assert r["login"]["accepted"] <= 3, r["login"]
    assert r["lookup"]["accepted"] == 0 and r["lookup"]["refused"] == 6, r["lookup"]
    for route in ("register", "login", "lookup"):
        assert r[route]["errors"] == 0, (route, r[route])


# ── 2 and 3. The first failure, not the second ──────────────────────────────

def test_the_first_failure_refuses_invite_lookup_and_tightens_login():
    r = _run("first_failure")
    assert r["lookup_before"] == 200, r
    assert r["lookup_first"] == 503, r
    assert r["login"]["accepted"] <= 3, r["login"]
    assert r["login"]["errors"] == 0, r["login"]


def test_a_write_failing_after_the_view_is_not_a_500_and_tightens_login():
    """Flask-Limiter deducts `deduct_when` limits AFTER the view; that write
    raised straight out of the after-request hook as a 500."""
    r = _run("deduct_write_fails")
    assert r["first"] == 401, r
    assert r["rest"]["errors"] == 0, r["rest"]
    assert r["rest"]["accepted"] <= 3, r["rest"]


# ── 4. No split allowance ───────────────────────────────────────────────────

def test_a_failure_does_not_restart_the_count():
    r = _run("split_at_failure")
    assert r["before"]["accepted"] == 4, r
    assert r["after"]["accepted"] == 1, r["after"]


def test_a_recovery_does_not_restart_the_count():
    r = _run("split_at_recovery", D7_FAST_RECOVERY="1", D7_RECOVERY_WAIT="3")
    assert r["during"]["accepted"] == 5, r
    assert r["after"]["accepted"] == 0, r["after"]


def test_flapping_never_buys_a_larger_allowance():
    """Measured on b045172 (real Valkey, flapping): 10 login guesses and 24
    lookups where 6541cae allowed 5 and 12."""
    r = _run("flapping")
    assert r["limiter_enabled_seen"] == [True], r
    assert r["register"]["accepted"] <= 5, r["register"]
    assert r["login"]["accepted"] <= 5, r["login"]
    assert r["events"]["accepted"] <= 30, r["events"]
    # Lookup: refused from the first failure; never more than its own limit.
    assert r["lookup"]["accepted"] <= 2, r["lookup"]
    for route in ("register", "login", "lookup", "events"):
        assert r[route]["errors"] == 0, (route, r[route])


# ── Bounded and quiet ───────────────────────────────────────────────────────

def test_a_hung_backend_is_bounded_and_still_limited():
    r = _run("hung", timeout=40)
    assert r["register"]["accepted"] == 5, r["register"]
    assert r["register"]["max_s"] < 1.5, r["register"]
    assert r["health"]["max_s"] < 0.5, r["health"]


def test_while_degraded_nothing_waits_on_the_backend_but_the_paced_probe():
    r = _run("degraded_is_quiet", timeout=40)
    assert r["health"]["max_s"] < 0.2, r
    assert r["self_s"] < 0.2, r
    assert r["rl_status"] == "FAIL" and "in-process authority" in r["rl_observed"], r
    assert r["policy"] == "PASS", r
    # Ten registrations inside one probe interval: no backend traffic at all.
    assert r["backend_commands_while_degraded"] == 0, r
    assert r["register"]["accepted"] == 4, r["register"]
