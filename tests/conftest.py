"""First test infrastructure in this codebase (R2.1 Team Foundations).

No pytest config existed before this; introduced specifically because the
R2.1 brief asked for automated tests as a deliverable distinct from manual
smoke verification. Uses a temp-file SQLite DB (not in-memory — the Flask
test client and app share one connection pool, and a temp file avoids the
threading edge cases in-memory SQLite can hit under that setup) and disables
rate limiting so cap-boundary tests can make the request volume they need
(e.g. creating 10+ teams to prove the free-tier cap) without tripping the
same per-minute limits real users would hit.
"""
import atexit
import os
import tempfile
import time

# Tests run in production's process timezone. The server's mission day is the
# UTC date of its clock's instant, and many tests seed rows with
# `datetime.date.today()`; in a local timezone those two disagree for several
# hours a day (every evening in the Americas), which used to make the suite
# fail depending on when it was run.
os.environ['TZ'] = 'UTC'
time.tzset()

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-not-for-production')

_db_fd, _db_path = tempfile.mkstemp(suffix='.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_db_path}'

# The QA tooling's provenance ledger (round 6): never the repo's instance/
# file during tests. One throwaway file per pytest process.
# Idempotent: two test files import this module a second time as
# `tests.conftest`, and a second ledger would split the tooling's records from
# what the cleanup reads.
_ledger_path = os.environ.get('_STREAKFIT_TEST_QA_LEDGER')
if not _ledger_path:
    _ledger_fd, _ledger_path = tempfile.mkstemp(suffix='.qa-ledger.jsonl')
    os.close(_ledger_fd)
    os.environ['_STREAKFIT_TEST_QA_LEDGER'] = _ledger_path
    atexit.register(lambda: os.path.exists(_ledger_path) and os.unlink(_ledger_path))
os.environ['STREAKFIT_QA_LEDGER'] = _ledger_path


def _remove_test_db():
    # One file per pytest process used to stay behind in $TMPDIR forever; on a
    # tmpfs /tmp, ~2,500 of them (~1 GB) filled the per-user quota (2026-10-15).
    try:
        os.close(_db_fd)
    except OSError:
        pass
    for suffix in ('', '-journal', '-wal', '-shm'):
        try:
            os.unlink(_db_path + suffix)
        except FileNotFoundError:
            pass


atexit.register(_remove_test_db)

# E402: these imports must run AFTER the os.environ setup above — importing app
# reads SECRET_KEY/DATABASE_URL at module scope, so hoisting them breaks the suite.
import pytest  # noqa: E402

from app import app as flask_app, db as _db, limiter as _limiter  # noqa: E402

# app.config['RATELIMIT_ENABLED'] = False alone doesn't work here: Flask-Limiter
# reads it once at Limiter(app=app, ...) init time, which already happened at
# `import app` above, before any test fixture runs. Disabling the limiter
# instance directly is what actually takes effect.
_limiter.enabled = False


@pytest.fixture()
def app():
    flask_app.config['TESTING'] = True
    open(_ledger_path, 'w').close()     # each test starts with an empty ledger

    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()
    # Set by the QA cleanup (round 6); never carried into the next test.
    flask_app.config.pop('QA_PROVENANCE', None)


@pytest.fixture()
def client(app):
    return app.test_client()


def qa_tooling_created(user_id, username):
    """What the QA tooling does when it creates a qa_smoke_ account (round 6):
    records (id, username) in its provenance ledger. Also mirrored into the
    app config, as scripts/cleanup_qa_smoke.py does from the ledger."""
    import json as _json
    with open(_ledger_path, 'a') as fh:
        fh.write(_json.dumps({'user_id': user_id, 'username': username}) + '\n')
    prov = dict(flask_app.config.get('QA_PROVENANCE') or {})
    prov[user_id] = username
    flask_app.config['QA_PROVENANCE'] = prov


def register_and_login(client, username, password='WalkTest123!'):
    """Registers a user and returns their JWT access token.

    A qa_smoke_ name is registered the way the QA tooling does it (the
    reserved prefix needs its provisioning token, round 6) and recorded in
    the provenance ledger. A test of the reservation itself posts directly.
    """
    headers = {}
    if username.startswith('qa_smoke_'):
        headers['X-StreakFit-QA-Provisioning'] = flask_app.config['QA_IN_PROCESS_TOKEN']
    r = client.post('/api/register', json={'username': username, 'password': password},
                    headers=headers)
    if headers and r.status_code == 201:
        qa_tooling_created(r.get_json()['user_id'], username)
    resp = client.post('/api/login', json={'username': username, 'password': password})
    return resp.get_json()['access_token']


def auth_headers(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture(autouse=True)
def _clear_weather_caches():
    """Weather caches are module-level globals; clear them before each test so one
    test's cached city can't leak into another."""
    import app as _app
    _app._GEOCODE_CACHE.clear()
    _app._FORECAST_CACHE.clear()
    yield


def mission_json(day=None, **extra):
    """Request body naming the mission day a write is for. Since Step 1 every
    daily completion and Brain Boost answer must say which day it belongs to,
    so a page left open across midnight cannot be credited to the next day.
    Defaults to the server's current mission day."""
    import app as appmod
    return {'mission_date': (day or appmod.server_today()).isoformat(), **extra}


@pytest.fixture(autouse=True)
def no_sweeper_thread_survives_its_test():
    """A test that starts the real retention sweeper must stop it before it
    ends. A surviving thread keeps writing retention/notification runs into
    LATER tests' databases, so their results depend on timing (CI, 2026-10-14:
    test_self_check_fails_when_the_sweep_has_gone_quiet saw a fresh run that it
    had just deleted). Any survivor is stopped here and the test that leaked it
    fails, deterministically, instead of a random later test."""
    import threading
    yield
    leaked = [t for t in threading.enumerate() if t.name.startswith('streakfit-retention') and t.is_alive()]
    for t in leaked:
        stop = getattr(t, 'stop_event', None)
        if stop is not None:
            stop.set()
            t.join(timeout=10)
    stuck = [t.name for t in leaked if t.is_alive()]
    assert not leaked, (f'{len(leaked)} retention sweeper thread(s) still running at the end of this test'
                        + (f'; {len(stuck)} did not stop within 10 s: {stuck}' if stuck else ''))
