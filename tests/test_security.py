"""Security regression tests for the hardening added in the WS10 audit pass:
constant-time admin-secret compare, security headers, request-size limit, and
login timing equalization -- plus the ProxyFix client-IP handling the per-IP
rate limits depend on.
"""
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.test import EnvironBuilder

import app as appmod


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


# ── ProxyFix: the rate limiter must key on the client, not on Render's edge ────
# Before this, request.remote_addr was gunicorn's TCP peer -- measured in
# production as 10.26.173.131, a Render-internal address -- so every per-IP rate
# limit was keyed on infrastructure. Measured effect: 12 POSTs over one
# keep-alive connection hit /api/register's 5/min exactly, while spreading across
# connections allowed 30 of 50 and 37 of 60, i.e. ~6-7x every configured limit.
#
# These exercise the ACTUAL configured middleware instance on the app object, so
# they fail if x_for is ever changed, rather than re-declaring a ProxyFix that
# would pass regardless of what production runs.


def _seen_remote_addr(xff=None):
    """REMOTE_ADDR as the app sees it after the real, configured ProxyFix runs."""
    proxy_fix = appmod.app.wsgi_app
    assert isinstance(proxy_fix, ProxyFix), (
        "app.wsgi_app is not wrapped in ProxyFix -- per-IP rate limits would be "
        "keyed on Render's internal address again"
    )

    captured = {}

    def sink(environ, start_response):
        captured["ip"] = environ.get("REMOTE_ADDR")
        start_response("204 No Content", [])
        return [b""]

    wrapped = proxy_fix.app
    proxy_fix.app = sink
    try:
        headers = {"X-Forwarded-For": xff} if xff else {}
        env = EnvironBuilder("/", headers=headers).get_environ()
        env["REMOTE_ADDR"] = "10.26.173.131"   # what Render's edge really presents
        proxy_fix(env, lambda *a, **k: None)
    finally:
        proxy_fix.app = wrapped
    return captured.get("ip")


def test_proxyfix_is_configured_for_exactly_two_hops():
    """client -> Cloudflare -> Render internal -> gunicorn. Two trusted
    appenders, so the client is the second entry from the right."""
    assert appmod.app.wsgi_app.x_for == 2


def test_proxyfix_picks_the_client_from_a_two_hop_chain():
    """The real production shape, second entry inside Cloudflare's 104.16.0.0/13."""
    assert _seen_remote_addr("203.0.113.7, 104.23.243.118") == "203.0.113.7"


def test_proxyfix_ignores_a_forged_leading_entry():
    """THE security property. Cloudflare appends the connecting IP to any
    X-Forwarded-For a caller supplies, so a forged value lands further left and
    x_for=2 still resolves to the real client. If this regresses, any caller can
    choose its own rate-limit bucket and evade limits entirely."""
    assert _seen_remote_addr("1.2.3.4, 203.0.113.7, 104.23.243.118") == "203.0.113.7"


def test_proxyfix_never_trusts_the_leftmost_entry():
    """Render's own guidance says the first IP is the real client. Per the append
    behaviour above that entry is attacker-controlled -- pin that we never use it."""
    assert _seen_remote_addr("1.2.3.4, 203.0.113.7, 104.23.243.118") != "1.2.3.4"


def test_proxyfix_fails_open_not_spoofable_on_a_short_chain():
    """Fewer than two hops means an unexpected topology. ProxyFix leaves
    REMOTE_ADDR alone, so we degrade to the old over-permissive key rather than
    to an attacker-supplied one."""
    assert _seen_remote_addr("1.2.3.4") == "10.26.173.131"
    assert _seen_remote_addr(None) == "10.26.173.131"


def test_proxyfix_tolerates_whitespace_in_the_chain():
    """Real chains arrive with inconsistent spacing; hop indices must not shift."""
    assert _seen_remote_addr(" 203.0.113.7 , 104.23.243.118 ") == "203.0.113.7"


def test_limiter_key_follows_the_client_not_the_edge(client):
    """Tie it to the purpose: flask-limiter's key function buckets requests, so
    it is the thing that must return the client IP."""
    from flask_limiter.util import get_remote_address

    with appmod.app.test_request_context(
        "/", environ_overrides={"REMOTE_ADDR": "198.51.100.22"}
    ):
        assert get_remote_address() == "198.51.100.22"
