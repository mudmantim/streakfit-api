"""D7: a hung or unreachable Redis must fail fast, never freeze the one sync worker.

Production runs ONE sync gunicorn worker with RATELIMIT_STORAGE_URI pointing at
Redis. redis-py's defaults are socket_timeout=None and socket_connect_timeout=None,
so a backend that accepts a connection and never answers -- or whose host never
answers a SYN -- blocked every request touching the limiter (and, through the
15 s health probe, /health itself) until gunicorn killed the worker after 30 s,
over and over. Measured in the lab on 6541cae: 10 worker kills in two minutes
with a paused Valkey; a total outage with an unreachable host.

These tests reproduce both shapes WITHOUT a container:
  * HUNG  -- a TCP server that accepts and never replies;
  * BLACKHOLE -- a listener whose backlog is already full, so the kernel drops
    further SYNs and connect() hangs, exactly like an unanswering host.
Each scenario runs the real app in a SUBPROCESS (the limiter is built at import
from RATELIMIT_STORAGE_URI) under a hard timeout, so the deployed code's hang
shows up as a failed test rather than a hung suite.

What "fixed" means here is BOUNDED, not "healthy": requests may degrade exactly
as the existing outage policy says (login strict cap, lookup 503, the
self-check reporting FAIL) -- they must just never wait unboundedly.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Generous against the 0.5 s timeouts (login can pay up to ~3 bounded calls),
# and an order of magnitude under gunicorn's 30 s worker timeout.
BOUND_S = 4.0
HARD_TIMEOUT_S = 45


class _HungRedis:
    """Accepts connections, reads whatever arrives, never answers."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.conns = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                c, _ = self.sock.accept()
                self.conns.append(c)
            except OSError:
                continue

    def close(self):
        self._stop = True
        for c in self.conns:
            c.close()
        self.sock.close()


class _BlackholeRedis:
    """A listener that is never accepted from, its backlog filled up front, so
    the kernel silently drops further SYNs: connect() blocks, as it does for a
    host that never answers."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(0)
        self.port = self.sock.getsockname()[1]
        self.fillers = []
        for _ in range(8):           # fill the accept queue until a connect would block
            s = socket.socket()
            s.setblocking(False)
            try:
                s.connect(('127.0.0.1', self.port))
            except (BlockingIOError, OSError):
                pass
            self.fillers.append(s)

    def connect_blocks(self):
        s = socket.socket()
        s.settimeout(1.0)
        try:
            s.connect(('127.0.0.1', self.port))
            return False
        except (socket.timeout, TimeoutError):
            return True
        except OSError:
            return False
        finally:
            s.close()

    def close(self):
        for s in self.fillers:
            s.close()
        self.sock.close()


SCENARIO = r'''
import json, os, sys, time
sys.path.insert(0, os.environ['REPO'])
import app as A
A.limiter.enabled = True
with A.app.app_context():
    A.db.create_all()
c = A.app.test_client()
out = {}
def timed(name, fn):
    t = time.monotonic()
    try:
        r = fn()
        code = r.status_code
    except Exception as e:
        code = type(e).__name__
    out[name] = [code, round(time.monotonic() - t, 2)]
timed('health_first', lambda: c.get('/health'))
timed('api_health', lambda: c.get('/api/health'))
timed('login', lambda: c.post('/api/login', json={'username': 'x', 'password': 'y'}))
timed('lookup', lambda: c.get('/api/teams/lookup/ABCDEFGH'))
timed('health_again', lambda: c.get('/health'))
def self_check():
    r = c.get('/api/verification/self')
    out['self_checks'] = {k['id']: k['status'] for k in r.get_json()['checks']
                          if k['id'].startswith('ratelimit.')}
    return r
timed('self', self_check)
out['opts'] = getattr(getattr(getattr(A.limiter.storage, 'storage', None), 'connection_pool', None),
                      'connection_kwargs', None)
out['opts'] = {k: out['opts'].get(k) for k in ('socket_timeout', 'socket_connect_timeout',
                                                'retry_on_timeout')} if out['opts'] else None
print('RESULT ' + json.dumps(out))
'''


def run_scenario(uri):
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)
    env = {**os.environ, 'REPO': REPO, 'RATELIMIT_STORAGE_URI': uri,
           'DATABASE_URL': f'sqlite:///{db_path}', 'SECRET_KEY': 'test-d7',
           'JWT_SECRET_KEY': 'test-d7', 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    try:
        p = subprocess.run([sys.executable, '-c', SCENARIO], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=HARD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pytest.fail(f'the app did not answer within {HARD_TIMEOUT_S}s: an unbounded '
                    f'Redis wait froze it (D7)')
    finally:
        os.unlink(db_path)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith('RESULT ')]
    assert line, p.stdout[-2000:] + p.stderr[-3000:]
    return json.loads(line[0][7:])


def assert_bounded(res):
    for name in ('health_first', 'api_health', 'login', 'lookup', 'health_again', 'self'):
        code, seconds = res[name]
        assert seconds < BOUND_S, (name, res)
        assert code not in (500, 'TimeoutError'), (name, res)


@pytest.fixture()
def hung():
    srv = _HungRedis()
    yield srv
    srv.close()


@pytest.fixture()
def blackhole():
    srv = _BlackholeRedis()
    if not srv.connect_blocks():
        srv.close()
        pytest.skip('this kernel answers a full backlog instead of dropping the SYN')
    yield srv
    srv.close()


def test_a_hung_redis_never_freezes_a_request(hung):
    res = run_scenario(f'redis://127.0.0.1:{hung.port}/0')
    assert_bounded(res)
    # The degraded policy still applies: the self-check reports it, honestly.
    assert res['self_checks']['ratelimit.shared_storage'] == 'FAIL'


def test_an_unreachable_redis_host_never_freezes_a_request(blackhole):
    res = run_scenario(f'redis://127.0.0.1:{blackhole.port}/0')
    assert_bounded(res)
    assert res['self_checks']['ratelimit.shared_storage'] == 'FAIL'


@pytest.mark.parametrize('scheme', ['redis', 'rediss'])
def test_the_redis_client_is_built_with_bounded_io(scheme):
    # No server needed: nothing connects until a command is sent; opts are read
    # from the real pool the limiter built.
    res = run_scenario_opts(f'{scheme}://127.0.0.1:1/0')
    assert res == {'socket_timeout': 0.5, 'socket_connect_timeout': 0.5,
                   'retry_on_timeout': False}, res


def test_a_uri_that_overrides_the_timeouts_is_reported(hung):
    """redis-py lets query parameters override constructor options. A URI with
    ?socket_timeout=9 would silently undo the bound, so the self-check reads the
    EFFECTIVE options and says so."""
    res = run_scenario(f'redis://127.0.0.1:{hung.port}/0?socket_timeout=9')
    assert res['self_checks']['ratelimit.bounded_io'] == 'FAIL', res


def test_memory_storage_is_unaffected_and_reports_bounded_io_not_applicable():
    res = run_scenario('memory://')
    assert_bounded(res)
    assert res['opts'] is None
    assert res['self_checks']['ratelimit.bounded_io'] == 'PASS', res


OPTS_ONLY = r'''
import json, os, sys
sys.path.insert(0, os.environ['REPO'])
import app as A
kw = A.limiter.storage.storage.connection_pool.connection_kwargs
print('RESULT ' + json.dumps({k: kw.get(k) for k in
      ('socket_timeout', 'socket_connect_timeout', 'retry_on_timeout')}))
'''


def run_scenario_opts(uri):
    env = {**os.environ, 'REPO': REPO, 'RATELIMIT_STORAGE_URI': uri,
           'DATABASE_URL': 'sqlite://', 'SECRET_KEY': 'test-d7', 'JWT_SECRET_KEY': 'test-d7',
           'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('STREAKFIT_ENFORCE_DB_HEAD', None)
    env.pop('STREAKFIT_RETENTION_SWEEPER', None)
    p = subprocess.run([sys.executable, '-c', OPTS_ONLY], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=HARD_TIMEOUT_S)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith('RESULT ')]
    assert line, p.stdout[-2000:] + p.stderr[-3000:]
    return json.loads(line[0][7:])


def test_the_app_probe_does_not_swallow_a_worker_abort(monkeypatch):
    """gunicorn aborts a stuck worker by raising SystemExit in it. `limits`'
    RedisStorage.check() wraps PING in a bare `except:`, which swallowed that
    abort (measured: a request answered 200 after its worker was told to die).
    The app's own probe pings under `except Exception` instead."""
    import app as appmod

    class Conn:
        def ping(self):
            raise SystemExit(1)

    class Storage:
        def get_connection(self):
            return Conn()

        def check(self):
            try:
                return Conn().ping()
            except:  # noqa: E722  (exactly what limits does)
                return False

        def incr(self, *a, **kw):
            return 1

    monkeypatch.setattr(type(appmod.limiter), 'storage', property(lambda self: Storage()))
    with pytest.raises(SystemExit):
        appmod._ratelimit_backend_check()
