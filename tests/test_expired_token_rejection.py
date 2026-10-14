"""Expired and foreign-signed tokens cannot authenticate anywhere.

Context, 2026-10-14: Gitleaks found two access tokens committed in
photostate.json (2026-09-18), expired two hours after issue.

Every route is CLASSIFIED below, by endpoint name, and the classification is
checked against the source, so a new route fails this file until someone
decides which class it belongs to:

  PROTECTED  @jwt_required(): an expired or foreign token is refused exactly as
             no token is (401/422), and a live token is not.
  ADMIN      _require_admin_secret() (X-Admin-Secret): a token of any kind
             changes nothing.
  PUBLIC     no authentication: any token, expired or foreign, gets exactly what
             no token gets.
  OPTIONAL   not a route class but a code path: user_or_ip_key, the rate-limit
             key, reads a token if one is present. An expired or foreign token
             must fall back to the IP key, never to the user's quota.

No external service is invoked: provider keys are removed, and any outbound
connection attempt is refused AND counted. Each sweep asserts the count is zero.
"""
import inspect
import re
import socket
from datetime import timedelta

import jwt as pyjwt
import pytest
from flask_jwt_extended import create_access_token

from tests.conftest import auth_headers, register_and_login

PROTECTED = {
    'list_my_appeals', 'create_appeal', 'list_blocks', 'create_block', 'delete_block', 'answer_brain_boost',
    'list_challenge_presets', 'create_challenge', 'get_challenges', 'get_challenge', 'rename_challenge',
    'delete_challenge', 'check_in', 'coach', 'forget_coach_memory', 'get_daily', 'complete_daily_exercise',
    'set_daily_effort', 'get_me', 'update_me', 'delete_my_account', 'export_my_data', 'get_memory_book',
    'list_my_moderation_decisions', 'list_photo_filters', 'unlock_photo_filter', 'create_report', 'create_team',
    'list_teams', 'get_team', 'get_team_campfire', 'create_team_challenge', 'complete_team_challenge',
    'join_team', 'leave_team', 'remove_team_member', 'get_team_messages', 'post_team_message',
    'get_team_moments', 'upload_team_photo', 'get_team_photo', 'delete_team_photo', 'rotate_team_invite',
    'lookup_team_by_code',
}
ADMIN = {
    'admin_list_appeals', 'admin_decide_appeal', 'admin_project_status', 'admin_list_reports',
    'admin_report_detail', 'admin_report_action', 'admin_legal_hold', 'admin_photo_evidence', 'admin_stats',
    'admin_system_health', 'admin_verify_start', 'admin_verify_history', 'admin_verify_status',
}
PUBLIC = {
    'frontend', 'admin_dashboard',      # HTML shells; /admin's data comes only from ADMIN routes
    'service_worker', 'health', 'api_health', 'build_identity', 'verification_self',
    'get_demo_daily', 'record_event', 'register', 'login',
}
DENIED = {401, 422}
ADMIN_SECRET = 'admin-test-secret-not-for-production'


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No provider keys, no outbound connections; attempts are counted."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(repr(args[-1])[:80] if args else '?')
        raise OSError('network disabled in this test')
    monkeypatch.setattr(socket, 'create_connection', refuse)
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    for key in ('ANTHROPIC_API_KEY', 'RESEND_API_KEY', 'RATELIMIT_STORAGE_URI', 'STREAKFIT_PUBLIC_URL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ADMIN_SECRET', ADMIN_SECRET)
    return attempts


def _source(app, endpoint):
    return inspect.getsource(inspect.unwrap(app.view_functions[endpoint]))


def _routes(app):
    """(endpoint, method, url) for every route, with placeholder path values."""
    from flask import url_for
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        values = {arg: (1 if type(rule._converters[arg]).__name__ == 'IntegerConverter' else 'x')
                  for arg in rule.arguments}
        with app.test_request_context():
            url = url_for(rule.endpoint, **values)
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            yield rule.endpoint, method, url


def _tokens(app, client):
    live = register_and_login(client, 'expiry_probe')
    uid = client.get('/api/me', headers=auth_headers(live)).get_json()['id']
    with app.app_context():
        expired_now = create_access_token(identity=str(uid), expires_delta=timedelta(seconds=-1))
        expired_long = create_access_token(identity=str(uid), expires_delta=timedelta(days=-26))
    claims = pyjwt.decode(live, options={'verify_signature': False})
    foreign = pyjwt.encode(claims, 'some-other-servers-key-0123456789abcdef', algorithm='HS256')
    return uid, live, {'expired 1 s ago': expired_now, 'expired 26 days ago': expired_long,
                       'signed with another key': foreign}


# --- the classification itself ------------------------------------------------

def test_every_route_is_classified_exactly_once(app):
    endpoints = {e for e, _, _ in _routes(app)}
    assert not (PROTECTED & ADMIN or PROTECTED & PUBLIC or ADMIN & PUBLIC)
    assert endpoints - (PROTECTED | ADMIN | PUBLIC) == set(), 'unclassified route(s): add each to one class'
    assert (PROTECTED | ADMIN | PUBLIC) - endpoints == set(), 'classified route(s) that no longer exist'


def test_the_classification_matches_the_source(app):
    for endpoint in PROTECTED | ADMIN | PUBLIC:
        src = _source(app, endpoint)
        decorators = re.findall(r'^\s*@([\w.]+)', src, re.M)
        requires_jwt = 'jwt_required' in decorators
        requires_admin = '_require_admin_secret()' in src
        reads_token = bool(re.search(r'verify_jwt_in_request|get_jwt_identity|get_jwt\(', src))
        if endpoint in PROTECTED:
            assert requires_jwt and not requires_admin, endpoint
        elif endpoint in ADMIN:
            assert requires_admin and not requires_jwt and not reads_token, endpoint
        else:
            assert not requires_jwt and not requires_admin and not reads_token, endpoint


# --- behaviour, per class -----------------------------------------------------

def test_protected_routes_refuse_expired_and_foreign_tokens_and_accept_a_live_one(app, client, isolated):
    _, live, bad = _tokens(app, client)
    for endpoint, method, url in _routes(app):
        if endpoint not in PROTECTED:
            continue
        assert client.open(url, method=method).status_code == 401, (method, url)
        for label, token in bad.items():
            r = client.open(url, method=method, headers=auth_headers(token))
            expected = (401, 'Token has expired') if label.startswith('expired') else (422, 'Invalid token')
            assert (r.status_code, (r.get_json() or {}).get('error')) == expected, (method, url, label)
        got = client.open(url, method=method, headers=auth_headers(live)).status_code
        assert got not in DENIED, (method, url, 'a live token was refused, so this sweep proves nothing')
    assert isolated == [], f'outbound connection attempted: {isolated}'


def test_admin_routes_ignore_tokens_entirely(app, client, isolated):
    _, live, bad = _tokens(app, client)
    for endpoint, method, url in _routes(app):
        if endpoint not in ADMIN:
            continue
        for token in (live, *bad.values()):
            assert client.open(url, method=method, headers=auth_headers(token)).status_code == 403, (method, url)
        if method == 'GET':     # with the secret: read-only routes only, so nothing is started or changed
            with_secret = {'X-Admin-Secret': ADMIN_SECRET}
            plain = client.open(url, method=method, headers=with_secret).status_code
            for token in bad.values():
                assert client.open(url, method=method, headers={**with_secret, **auth_headers(token)}
                                   ).status_code == plain, (method, url)
    assert isolated == [], f'outbound connection attempted: {isolated}'


def test_public_routes_answer_a_bad_token_exactly_as_no_token(app, client, isolated):
    _, _, bad = _tokens(app, client)
    for endpoint, method, url in _routes(app):
        if endpoint not in PUBLIC:
            continue
        anon = client.open(url, method=method).status_code
        for label, token in bad.items():
            got = client.open(url, method=method, headers=auth_headers(token)).status_code
            assert got == anon, (method, url, label, anon, got)
    assert isolated == [], f'outbound connection attempted: {isolated}'


def test_the_rate_limit_key_never_credits_an_expired_or_foreign_token_to_the_user(app, client):
    from app import user_or_ip_key
    uid, live, bad = _tokens(app, client)
    ip = {'REMOTE_ADDR': '203.0.113.9'}
    with app.test_request_context('/api/coach', headers=auth_headers(live), environ_base=ip):
        assert user_or_ip_key() == f'user:{uid}'
    for label, token in bad.items():
        with app.test_request_context('/api/coach', headers=auth_headers(token), environ_base=ip):
            assert user_or_ip_key() == '203.0.113.9', label


def test_the_network_guard_really_catches_an_outbound_attempt(isolated):
    """Otherwise 'zero attempts' above would be vacuous. 192.0.2.1 is TEST-NET-1;
    the guard refuses before any packet is sent."""
    import urllib.request
    with pytest.raises(OSError):
        urllib.request.urlopen('http://192.0.2.1/', timeout=1)
    assert len(isolated) == 1
