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


def test_admin_route_non_ascii_secret_returns_403_not_500(client, monkeypatch):
    # Regression: hmac.compare_digest raises TypeError on non-ASCII str, so a
    # header value with high bytes used to crash the gate (500) instead of
    # failing closed. The corrected gate encodes to bytes first and returns 403.
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get('/api/admin/stats', headers={'X-Admin-Secret': 'wröng-ø-sécret'})
    assert r.status_code == 403


def test_oversized_request_body_rejected(client):
    big = '{"username":"' + 'a' * 300000 + '"}'
    r = client.post('/api/login', data=big, content_type='application/json')
    assert r.status_code == 413   # blocked by MAX_CONTENT_LENGTH before parsing


def test_login_nonexistent_user_returns_401_not_500(client):
    # exercises the dummy-hash timing-equalization path
    r = client.post('/api/login', json={'username': 'nobody-here', 'password': 'whatever123'})
    assert r.status_code == 401


# ── /api/admin/forwarded-chain — the proxy-chain diagnostic ───────────────────
# Read-only, admin-gated. It exists to measure the real X-Forwarded-For chain in
# production so ProxyFix's x_for can be set from evidence instead of a guess —
# guessing too high lets a client prepend a forged hop and choose its own rate
# limiter key. These tests pin the gate and the hop arithmetic.

def test_forwarded_chain_fails_closed_without_secret(client, monkeypatch):
    monkeypatch.delenv('ADMIN_SECRET', raising=False)
    assert client.get('/api/admin/forwarded-chain').status_code == 403


def test_forwarded_chain_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get('/api/admin/forwarded-chain', headers={'X-Admin-Secret': 'wrong'})
    assert r.status_code == 403


def test_forwarded_chain_counts_hops_from_the_right(client, monkeypatch):
    """x_for=N must select the Nth entry from the RIGHT, matching ProxyFix.

    This is the whole point of the endpoint: with 'client, edge1, edge2', the
    trustworthy client value is the 3rd from the right, and anything a caller
    prepends lands further left — never displacing what the proxies appended.
    """
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get(
        '/api/admin/forwarded-chain',
        headers={'X-Admin-Secret': 's3cret-value',
                 'X-Forwarded-For': '203.0.113.9, 10.0.0.1, 10.0.0.2'},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body['x_forwarded_for_hops'] == ['203.0.113.9', '10.0.0.1', '10.0.0.2']
    assert body['x_forwarded_for_hop_count'] == 3
    assert body['proxyfix_would_pick']['x_for=1'] == '10.0.0.2'
    assert body['proxyfix_would_pick']['x_for=2'] == '10.0.0.1'
    assert body['proxyfix_would_pick']['x_for=3'] == '203.0.113.9'
    # Asking for more hops than exist must be None, never a wrong-but-plausible IP.
    assert body['proxyfix_would_pick']['x_for=4'] is None


def test_forwarded_chain_handles_absent_headers(client, monkeypatch):
    """No XFF at all (direct connection) must not error, and must report the
    key the limiter uses today so the before/after is unambiguous."""
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get('/api/admin/forwarded-chain',
                   headers={'X-Admin-Secret': 's3cret-value'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['x_forwarded_for_raw'] is None
    assert body['x_forwarded_for_hops'] == []
    assert body['x_forwarded_for_hop_count'] == 0
    assert all(v is None for v in body['proxyfix_would_pick'].values())
    assert body['limiter_key_today']            # never empty — mirrors get_remote_address


def test_forwarded_chain_tolerates_whitespace_and_empty_entries(client, monkeypatch):
    """Real proxy chains arrive with inconsistent spacing; a stray empty entry
    must not shift the hop indices and mis-identify the client."""
    monkeypatch.setenv('ADMIN_SECRET', 's3cret-value')
    r = client.get(
        '/api/admin/forwarded-chain',
        headers={'X-Admin-Secret': 's3cret-value',
                 'X-Forwarded-For': ' 203.0.113.9 ,, 10.0.0.1 , '},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body['x_forwarded_for_hops'] == ['203.0.113.9', '10.0.0.1']
    assert body['proxyfix_would_pick']['x_for=1'] == '10.0.0.1'
    assert body['proxyfix_would_pick']['x_for=2'] == '203.0.113.9'
