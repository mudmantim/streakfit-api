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
import os
import tempfile

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-not-for-production')

_db_fd, _db_path = tempfile.mkstemp(suffix='.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_db_path}'

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

    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register_and_login(client, username, password='WalkTest123!'):
    """Registers a user and returns their JWT access token."""
    client.post('/api/register', json={'username': username, 'password': password})
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
