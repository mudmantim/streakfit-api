"""An expired or foreign-signed access token never buys more than no token.

Context, 2026-10-14: Gitleaks found two access tokens committed in
photostate.json (2026-09-18). They expired two hours after issue, so the
remaining question is whether the application ever accepts an expired token.
This sweeps EVERY route and method: whatever a request without a token gets,
the same request with an expired token gets the same or a 401/422, never more.

The network is blocked for the whole sweep, so no route can reach a provider.
"""
import socket
from datetime import timedelta

import jwt as pyjwt
import pytest
from flask_jwt_extended import create_access_token

from tests.conftest import auth_headers, register_and_login

DENIED = {401, 422}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError('network disabled in this test')
    monkeypatch.setattr(socket, 'create_connection', refuse)
    monkeypatch.setattr(socket.socket, 'connect', refuse)


def _user_id(client, token):
    return client.get('/api/me', headers=auth_headers(token)).get_json()['id']


def _tokens(app, client):
    live = register_and_login(client, 'expiry_probe')
    uid = _user_id(client, live)
    with app.app_context():
        expired_now = create_access_token(identity=str(uid), expires_delta=timedelta(seconds=-1))
        expired_long = create_access_token(identity=str(uid), expires_delta=timedelta(days=-26))
    claims = pyjwt.decode(live, options={'verify_signature': False})
    foreign = pyjwt.encode(claims, 'some-other-servers-key-0123456789abcdef', algorithm='HS256')
    return live, {'expired 1s ago': expired_now, 'expired 26 days ago': expired_long, 'foreign key': foreign}


def _requests(app):
    """Every (method, url) the app serves, with placeholder path arguments."""
    from flask import url_for
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        values = {arg: (1 if type(rule._converters[arg]).__name__ == 'IntegerConverter' else 'x')
                  for arg in rule.arguments}
        with app.test_request_context():
            url = url_for(rule.endpoint, **values)
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            yield method, url


def test_the_expired_token_is_rejected_with_the_expired_message(app, client):
    live, bad = _tokens(app, client)
    assert client.get('/api/me', headers=auth_headers(live)).status_code == 200
    r = client.get('/api/me', headers=auth_headers(bad['expired 1s ago']))
    assert r.status_code == 401 and r.get_json() == {'error': 'Token has expired'}
    assert client.get('/api/me', headers=auth_headers(bad['expired 26 days ago'])).status_code == 401
    assert client.get('/api/me', headers=auth_headers(bad['foreign key'])).status_code in DENIED


def test_no_route_gives_an_expired_or_foreign_token_more_than_no_token(app, client):
    live, bad = _tokens(app, client)
    protected, problems = 0, []
    for method, url in _requests(app):
        anon = client.open(url, method=method).status_code
        if anon in DENIED:
            protected += 1
        for label, token in bad.items():
            got = client.open(url, method=method, headers=auth_headers(token)).status_code
            if got != anon and got not in DENIED:
                problems.append(f'{method} {url}: no token -> {anon}, {label} -> {got}')
    assert not problems, '\n'.join(problems)
    # The sweep means something only if it actually reached protected routes.
    assert protected >= 40, protected   # 44 login-only routes on 2026-10-14
