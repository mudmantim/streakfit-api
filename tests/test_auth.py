"""Auth core — registration/login. Previously uncovered by unit tests.

The focus is the trust-breaker fix: a duplicate username (fast-path OR a
concurrent race that only the unique constraint catches at commit time) must
return a clean 400, never a raw 500.
"""
from sqlalchemy.exc import IntegrityError


def test_register_success_returns_201(client):
    resp = client.post('/api/register',
                       json={'username': 'newbie', 'password': 'ValidPass123'})
    assert resp.status_code == 201


def test_register_duplicate_username_fast_path_returns_400(client):
    client.post('/api/register', json={'username': 'dupe', 'password': 'ValidPass123'})
    resp = client.post('/api/register', json={'username': 'dupe', 'password': 'ValidPass123'})
    assert resp.status_code == 400
    assert 'taken' in resp.get_json()['error'].lower()


def test_register_commit_integrityerror_returns_400_not_500(client, monkeypatch):
    """Simulate the concurrent-signup race: the pre-check passes, then the
    commit hits the unique constraint. The old code raised a raw 500; the fix
    catches IntegrityError and returns the friendly 400."""
    import app as app_module

    def boom(*args, **kwargs):
        raise IntegrityError("INSERT INTO user", {}, Exception("UNIQUE constraint failed: user.username"))

    monkeypatch.setattr(app_module.db.session, 'commit', boom)
    resp = client.post('/api/register',
                       json={'username': 'racer', 'password': 'ValidPass123'})
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}"
    assert 'taken' in resp.get_json()['error'].lower()


def test_register_short_password_returns_400(client):
    resp = client.post('/api/register', json={'username': 'shorty', 'password': 'abc'})
    assert resp.status_code == 400
    assert '8 characters' in resp.get_json()['error']


def test_login_success_and_wrong_password(client):
    client.post('/api/register', json={'username': 'loginner', 'password': 'ValidPass123'})
    ok = client.post('/api/login', json={'username': 'loginner', 'password': 'ValidPass123'})
    assert ok.status_code == 200
    assert 'access_token' in ok.get_json()

    bad = client.post('/api/login', json={'username': 'loginner', 'password': 'WrongPass123'})
    assert bad.status_code == 401
