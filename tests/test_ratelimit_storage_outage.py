"""A rate-limit backend outage must not take the application down with it.

THE DEFECT. Stop the shared rate-limit backend under a running app and every
throttled route returned 500 for the next fifteen seconds. Measured on the
deployed stack -- Python 3.12.7, Flask-Limiter 3.5.0, limits 5.8.0 -- against
a real Valkey 8 container, which is what Render Key Value runs:

    t+0.8s   backend killed   /api/health 500  /api/login 500
    t+12.8s                   /api/health 500  /api/login 500
    t+16.1s                   /api/health 200  /api/login 401

Fifteen seconds is not a coincidence, it is _SHARED_RL_PROBE_TTL.
`_degrade_limiter_when_shared_storage_is_down` stands the limiter down when
the backend is unreachable, but it reads a probe cached for that long, so for
one TTL it kept serving the "healthy" it recorded moments before the backend
died -- left `limiter.enabled` True, and the limit raised straight through
Flask-Limiter's route decorator.

Why it mattered more than fifteen seconds suggests:

  * /api/health is what Render polls for liveness. A backend blip became a
    failing health check on the web service.
  * /api/verification/self is the endpoint whose job is to report "shared
    rate-limit storage is unreachable". It was taken out by the exact
    condition it exists to report -- a check that cannot run during the
    failure it detects.

And the free Render Key Value tier loses everything on restart, so this is not
a rare event on that plan; it is the documented behaviour.

THE FIX is `in_memory_fallback_enabled=True` on the Limiter, NOT
`swallow_errors`. The distinction is the entire point and these tests hold the
line on it: swallow_errors drops the limit and serves the request unlimited;
the fallback keeps limiting in this worker's memory. Degraded, not off.

These tests point the app at a closed port rather than stopping a container,
so they need no service and run anywhere. A refused connection is the same
thing the outage produced.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app as appmod


# Port 1 is privileged and nothing listens there: connections are refused
# immediately, which is a configured-but-unreachable backend without the wait.
DEAD_BACKEND = "redis://127.0.0.1:1"

PROBE = r"""
import json, logging, sys
from datetime import datetime
logging.disable(logging.CRITICAL)
import app as A
A.app.logger.disabled = True
with A.app.app_context():
    A.db.create_all()


def prime_stale_healthy():
    # Reproduce the actual failure condition: a cached "healthy" verdict that
    # is no longer true.
    #
    # This is the whole defect. Pointing the app at a dead backend from the
    # start does NOT reproduce it -- the first request probes, finds the
    # backend down, and _degrade_limiter_when_shared_storage_is_down stands
    # the limiter down before anything can raise. The 500s happened to an app
    # whose backend had been answering a moment ago, where the cache still
    # says so and the storage no longer agrees. So the cache is set the way a
    # just-succeeded probe would leave it, against a backend that is in fact
    # refused.
    with A._shared_rl_lock:
        A._shared_rl_state.update(checked_at=datetime.utcnow(), healthy=True)
    A.limiter.enabled = True


import os
PRIME = os.environ.get('STREAKFIT_PROBE_PRIME') == '1'


def prime():
    # Only the stale-cache phase primes. The settled phase lets the degrade
    # hook do its job, which is what produces the tighter per-route cap.
    if PRIME:
        prime_stale_healthy()


out = {}
with A.app.test_client() as c:
    prime()
    c.post('/api/register', json={'username': 'rl_probe', 'password': 'Correct1!pass'})
    prime()
    out['health'] = c.get('/api/health').status_code
    prime()
    r = c.get('/api/verification/self')
    out['self_status'] = r.status_code
    if r.status_code == 200:
        checks = {x['id']: x for x in r.get_json()['checks']}
        rl = checks.get('ratelimit.shared_storage', {})
        out['rl_status'] = rl.get('status')
        out['rl_observed'] = rl.get('observed')
        out['rl_critical'] = rl.get('critical')
    logins = []
    for _ in range(12):
        prime()
        logins.append(c.post('/api/login',
                             json={'username': 'rl_probe',
                                   'password': 'WRONG'}).status_code)
    out['logins'] = logins
print('RESULT ' + json.dumps(out))
"""


def _run(storage_uri, tmp_path, prime):
    """Import the app fresh with a given limiter backend and probe it.

    A subprocess because the limiter is constructed at import time from
    RATELIMIT_STORAGE_URI, and reloading the app module in-process rebinds
    globals the live Flask app still closes over -- see
    tests/test_error_shape_and_coach_config.py.
    """
    root = Path(appmod.__file__).resolve().parent
    env = dict(
        os.environ,
        SECRET_KEY="x",
        JWT_SECRET_KEY="x",
        RENDER="true",                       # look like production
        RATELIMIT_STORAGE_URI=storage_uri,
        DATABASE_URL=f"sqlite:///{tmp_path}/outage.db",
        STREAKFIT_PROBE_PRIME="1" if prime else "0",
    )
    env.pop("STREAKFIT_ENV", None)
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, f"probe produced no result:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return json.loads(line[-1][len("RESULT "):])


@pytest.fixture(scope="module")
def dead(tmp_path_factory):
    """The first seconds of an outage: the cached probe still says healthy.

    This is the window the 500s lived in, and the one
    _degrade_limiter_when_shared_storage_is_down cannot see into.
    """
    return _run(DEAD_BACKEND, tmp_path_factory.mktemp("dead"), prime=True)


@pytest.fixture(scope="module")
def settled(tmp_path_factory):
    """Later in the same outage: the probe has expired and re-run, so the
    degrade hook has stood the limiter down and the per-route degraded
    policies are in charge."""
    return _run(DEAD_BACKEND, tmp_path_factory.mktemp("settled"), prime=False)


# ── The regression itself ──────────────────────────────────────────────────

def test_health_endpoint_survives_a_dead_rate_limit_backend(dead):
    """THE REGRESSION. This returned 500, and Render polls it for liveness."""
    assert dead["health"] == 200, (
        "the health endpoint Render polls returned "
        f"{dead['health']} because the rate-limit backend was unreachable")


def test_self_check_survives_the_condition_it_reports(dead):
    """It returned 500 too -- knocked out by the outage it exists to report."""
    assert dead["self_status"] == 200, (
        "/api/verification/self returned "
        f"{dead['self_status']} during the outage it is supposed to describe")


def test_the_outage_is_actually_reported(dead):
    """Surviving is not enough. Staying up and saying nothing would be worse
    than the 500, which at least was noticeable."""
    assert dead["rl_status"] == "FAIL"
    assert dead["rl_critical"] is True
    assert "DEGRADED" in dead["rl_observed"]


# ── Degraded, not off ──────────────────────────────────────────────────────

def test_login_is_still_throttled_while_the_backend_is_down(dead):
    """The line between this fix and `swallow_errors`.

    If an unreachable backend meant unlimited password guessing, the outage
    would have turned a security control off and returned 200s while doing
    it -- strictly worse than failing loudly.
    """
    assert 429 in dead["logins"], (
        "no request was refused with the backend down: rate limiting is OFF, "
        f"not degraded. Observed: {dead['logins']}")


def test_login_still_works_for_the_honest_user(dead):
    """A rate limiter must not be able to lock everyone out of the product.
    The first attempts are answered normally (401 for a wrong password)."""
    assert dead["logins"][0] == 401, (
        f"first login attempt returned {dead['logins'][0]}, not 401")


def test_degraded_cap_is_tighter_than_the_healthy_one(settled):
    """The fallback counter is PER PROCESS, so with N workers an attacker gets
    N times the allowance. It is set tighter to partly offset that, and this
    asserts it did not silently loosen."""
    allowed = settled["logins"].index(429)
    assert allowed <= appmod._DEGRADED_LOGIN_LIMIT, (
        f"{allowed} attempts allowed before refusing, expected at most "
        f"{appmod._DEGRADED_LOGIN_LIMIT}")


# ── A backend that is up but cannot record ────────────────────────────────

def test_a_backend_that_pings_but_cannot_write_is_not_a_pass(client, monkeypatch):
    """The second false green this check has produced.

    Measured against Valkey 8 with `maxmemory` exceeded and `noeviction`,
    which is the shape of a memory-capped plan under load:

        PING          -> PONG
        SET anything  -> OOM command not allowed when used memory > 'maxmemory'

    The old probe was a PING, so this reported PASS, "shared backend
    reachable (redis)", while the backend could not record a single count and
    every limit had silently become per-worker. The app stayed up -- the
    in-memory fallback absorbed the write errors -- which is exactly what made
    it invisible.
    """
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379")
    monkeypatch.setenv("STREAKFIT_ENV", "production")

    class PingsButCannotWrite:
        def check(self):
            return True                      # the ping succeeds

        def incr(self, key, expiry, amount=1):
            raise MemoryError("OOM command not allowed when used memory > 'maxmemory'")

    monkeypatch.setattr(type(appmod.limiter), "storage",
                        property(lambda self: PingsButCannotWrite()))

    body = client.get("/api/verification/self").get_json()
    check = {c["id"]: c for c in body["checks"]}["ratelimit.shared_storage"]

    assert check["status"] == "FAIL", (
        "a backend that answers a ping but refuses every write was reported "
        f"as {check['status']} — {check['observed']}")
    assert check["critical"] is True
    assert "could not record a count" in check["observed"]


def test_the_probe_actually_writes(monkeypatch):
    """Guards the mechanism, not just the outcome: if the probe ever goes back
    to being a bare ping, the test above would still pass against a backend
    whose ping happens to fail, and this is what would catch it."""
    wrote = []

    class RecordsTheWrite:
        def check(self):
            return True

        def incr(self, key, expiry, amount=1):
            wrote.append((key, expiry))
            return 1

    monkeypatch.setattr(type(appmod.limiter), "storage",
                        property(lambda self: RecordsTheWrite()))

    assert appmod._ratelimit_backend_check() is True
    assert wrote, "the backend check never wrote anything — it is a ping again"
    key, expiry = wrote[0]
    assert key == appmod._RATELIMIT_PROBE_KEY
    assert expiry == appmod._RATELIMIT_PROBE_EXPIRY_S


# ── The configuration that produces all of the above ───────────────────────

def test_fallback_is_enabled_and_errors_are_not_swallowed():
    """Both halves, because either one alone is wrong.

    Without the fallback, a storage error is a 500. With `swallow_errors`, it
    is an unlimited request. The app needs the first and must never have the
    second.
    """
    assert appmod.limiter._in_memory_fallback_enabled is True, (
        "in_memory_fallback_enabled is off; a backend outage will 500 every "
        "throttled route again")
    assert appmod.limiter._swallow_errors is False, (
        "swallow_errors is on; a backend outage now serves throttled routes "
        "UNLIMITED, which is the outcome the limiter exists to prevent")
