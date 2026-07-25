"""Security regression tests for the hardening added in the WS10 audit pass:
constant-time admin-secret compare, security headers, request-size limit, and
login timing equalization.
"""


def test_security_headers_present(client):
    r = client.get('/health')
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r.headers.get('X-Frame-Options') == 'DENY'
    assert "frame-ancestors" in r.headers.get('Content-Security-Policy', '')
    assert 'max-age=' in r.headers.get('Strict-Transport-Security', '')


def test_admin_route_fails_closed_without_secret(client, monkeypatch):
    monkeypatch.delenv('ADMIN_SECRET', raising=False)
    assert client.get('/api/admin/stats').status_code == 403


def test_admin_route_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get('/api/admin/stats', headers={'X-Admin-Secret': 'wrong'})
    assert r.status_code == 403


def test_admin_route_accepts_correct_secret(client, monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get('/api/admin/stats', headers={'X-Admin-Secret': 's3cret-value'})
    assert r.status_code != 403   # gate passes (constant-time compare matched)


def test_oversized_request_body_rejected(client):
    big = '{"username":"' + 'a' * 300000 + '"}'
    r = client.post('/api/login', data=big, content_type='application/json')
    assert r.status_code == 413   # blocked by MAX_CONTENT_LENGTH before parsing


def test_login_nonexistent_user_returns_401_not_500(client):
    # exercises the dummy-hash timing-equalization path
    r = client.post('/api/login', json={'username': 'nobody-here', 'password': 'whatever123'})
    assert r.status_code == 401
